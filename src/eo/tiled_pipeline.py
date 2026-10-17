
"""Sequential tile-based processing for large Sentinel-2 AOIs."""

import gc
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import rioxarray
from rio_cogeo.cogeo import cog_validate

from eo.tiling import build_aoi_tiles, choose_target_crs
from eo.sentinel2 import load_sentinel2_items
from eo.masking import mask_with_scl
from eo.composites import create_temporal_composite
from eo.processor import process_data
from eo.export import export_cog
from eo.metadata import create_metadata, save_metadata


OUTPUT_NAMES = {
    "RGB": "rgb.tif",
    "FALSE_COLOR": "false_color.tif",
    "NDVI": "ndvi.tif",
    "NDWI": "ndwi.tif",
    "NDMI": "ndmi.tif",
    "NBR": "nbr.tif",
    "NBR2": "nbr2.tif",
}


def _scale_tile_from_metadata(data, items, tile_id):
    """Apply each loaded band's consistent STAC scale and offset."""

    spectral_bands = [
        "red", "green", "blue", "nir", "swir16", "swir22"
    ]

    scaling_started = time.perf_counter()

    for band_name in spectral_bands:
        if band_name not in data.data_vars:
            continue

        parameters = set()

        for item in items:
            asset = item.assets.get(band_name)

            if asset is None:
                raise RuntimeError(
                    f"{tile_id}: missing {band_name} asset "
                    f"in scene {item.id}."
                )

            raster_bands = (
                asset.extra_fields.get("raster:bands") or []
            )

            if not raster_bands:
                raise RuntimeError(
                    f"{tile_id}: missing raster:bands metadata "
                    f"for {band_name}, scene {item.id}."
                )

            metadata = raster_bands[0]
            scale = metadata.get("scale")
            offset = metadata.get("offset", 0.0)
            nodata = metadata.get("nodata", 0)

            if scale is None:
                raise RuntimeError(
                    f"{tile_id}: missing scale for {band_name}."
                )

            parameters.add((
                float(scale),
                float(0.0 if offset is None else offset),
                float(0.0 if nodata is None else nodata),
            ))

        if len(parameters) != 1:
            raise RuntimeError(
                f"{tile_id}: inconsistent scale, offset or "
                f"NoData metadata for {band_name}: {parameters}"
            )

        scale, offset, nodata = next(iter(parameters))

        raw = data[band_name]
        valid = raw.notnull() & (raw != nodata)

        data[band_name] = (
            raw.astype("float32") * scale + offset
        ).where(valid)

    print(
        f"  Scaling setup time: "
        f"{time.perf_counter() - scaling_started:.2f} seconds"
    )

    return data


def process_tiled_aoi(
    *,
    items,
    aoi_shape,
    process,
    composite_method,
    start_date,
    end_date,
    cloud_cover_threshold,
    reference_item,
    output_dir,
    project_root,
    input_parameters,
    tile_size_m=5000,
):
    """
    Process a large AOI sequentially, one spatial tile at a time.

    The final product uses the existing output filename and metadata
    schema. Detailed before/after previews are deliberately omitted
    to avoid redundant full-area computations.
    """

    process = str(process).strip().upper()
    composite_method = str(composite_method).strip().lower()

    if process == "QUALITY_MASK":
        raise NotImplementedError(
            "QUALITY_MASK is not implemented by the current "
            "product dispatcher. Select another product."
        )

    if process not in OUTPUT_NAMES:
        raise ValueError(f"Unsupported product: {process}")

    if not items:
        raise ValueError("No Sentinel-2 scenes were supplied.")

    output_dir = Path(output_dir)
    project_root = Path(project_root)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Verify mosaic tools before spending time processing tiles.
    gdalbuildvrt = shutil.which("gdalbuildvrt")
    gdal_translate = shutil.which("gdal_translate")

    if not gdalbuildvrt or not gdal_translate:
        raise RuntimeError(
            "gdalbuildvrt and gdal_translate are required "
            "to mosaic the tile outputs. Install GDAL tools "
            "and ensure both commands are on PATH."
        )

    target_crs = choose_target_crs(items, aoi_shape)

    tiles = build_aoi_tiles(
        aoi_shape,
        tile_size_m=tile_size_m,
        target_crs=target_crs,
    )

    if not tiles:
        raise RuntimeError("No spatial tiles intersect the AOI.")

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        output_dir
        / "tiles"
        / f"{process.lower()}_{run_stamp}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)

    print("\nTILED PROCESSING")
    print("================")
    print("Product:", process)
    print("Target CRS:", target_crs)
    print("Tile size:", tile_size_m, "metres")
    print("Tiles:", len(tiles))
    print("Scenes:", len(items))
    print("Tile output folder:", run_dir)

    tile_files = []
    tile_records = []
    overall_started = time.perf_counter()

    for index, tile in enumerate(tiles, start=1):
        tile_started = time.perf_counter()
        tile_id = tile["tile_id"]

        print("\n" + "=" * 60)
        print(f"TILE {index}/{len(tiles)}: {tile_id}")
        print("Bounds:", tile["bounds"])
        print("=" * 60)

        # Load only this tile; imagery stays Dask-backed until needed.
        stage_started = time.perf_counter()

        data = load_sentinel2_items(
            items=items,
            geometry=None,
            requested_product=process,
            tile_bounds=tile["bounds"],
            target_crs=target_crs,
        )

        print(
            f"  Load setup time: "
            f"{time.perf_counter() - stage_started:.2f} seconds"
        )

        if data.rio.crs is None:
            raise RuntimeError(f"{tile_id}: loaded data has no CRS.")

        # Clip to this tile's exact intersection with the requested AOI.
        stage_started = time.perf_counter()

        tile_geometry = tile["aoi_geometry_wgs84"]

        data = data.rio.clip(
            geometries=[tile_geometry.__geo_interface__],
            crs="EPSG:4326",
            drop=False,
            all_touched=False,
        )

        print(
            f"  AOI clipping time: "
            f"{time.perf_counter() - stage_started:.2f} seconds"
        )

        data = _scale_tile_from_metadata(data, items, tile_id)

        # Apply the existing SCL policy before compositing.
        stage_started = time.perf_counter()
        masked_data = mask_with_scl(data)

        print(
            f"  SCL masking setup time: "
            f"{time.perf_counter() - stage_started:.2f} seconds"
        )

        processing_bands = [
            band
            for band in [
                "red", "green", "blue", "nir", "swir16", "swir22"
            ]
            if band in masked_data.data_vars
        ]

        # Calculate one temporal composite for this tile.
        stage_started = time.perf_counter()

        composite = create_temporal_composite(
            masked_data[processing_bands],
            method=composite_method,
        )

        print(
            f"  Composite setup time: "
            f"{time.perf_counter() - stage_started:.2f} seconds"
        )

        # Calculate the requested product.
        stage_started = time.perf_counter()
        result = process_data(composite, process)

        if result is None:
            raise RuntimeError(
                f"{tile_id}: {process} returned no result."
            )

        print(
            f"  Product calculation setup time: "
            f"{time.perf_counter() - stage_started:.2f} seconds"
        )

        # Export once. This usually triggers the deferred Dask work.
        tile_file = (
            run_dir / f"{process.lower()}_{tile_id}.tif"
        )
        stage_started = time.perf_counter()

        export_cog(result, tile_file)

        export_seconds = time.perf_counter() - stage_started
        print(f"  COG export time: {export_seconds:.2f} seconds")

        if not tile_file.is_file():
            raise RuntimeError(
                f"{tile_id}: COG export did not create {tile_file}."
            )

        with rioxarray.open_rasterio(
            tile_file, masked=True
        ) as raster:
            if raster.rio.crs is None:
                raise RuntimeError(
                    f"{tile_id}: exported tile has no CRS."
                )

            print("  Output dimensions:", raster.sizes)
            print("  Output CRS:", raster.rio.crs)
            print("  Output resolution:", raster.rio.resolution())

        tile_elapsed = time.perf_counter() - tile_started

        tile_files.append(tile_file)
        tile_records.append({
            "tile_id": tile_id,
            "bounds": list(tile["bounds"]),
            "file": str(tile_file),
            "elapsed_seconds": round(tile_elapsed, 3),
            "export_seconds": round(export_seconds, 3),
        })

        # Release tile variables before processing the next tile.
        del data, masked_data, composite, result
        gc.collect()

        print(
            f"Completed tile {index}/{len(tiles)}: {tile_id} "
            f"| elapsed: {tile_elapsed:.2f} seconds"
        )

    # Save a manifest specific to this run.
    manifest = {
        "product": process,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "composite_method": composite_method,
        "cloud_cover_threshold_percent": float(
            cloud_cover_threshold
        ),
        "target_crs": target_crs,
        "tile_size_m": tile_size_m,
        "scene_ids": [item.id for item in items],
        "tiles": tile_records,
    }

    manifest_file = run_dir / "tile_manifest.json"

    with manifest_file.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2)

    # Assemble one final mosaic from only this run's tile list.
    vrt_file = run_dir / f"{process.lower()}_mosaic.vrt"
    output_file = output_dir / OUTPUT_NAMES[process]

    subprocess.run(
        [
            gdalbuildvrt,
            "-overwrite",
            "-srcnodata", "-9999",
            "-vrtnodata", "-9999",
            str(vrt_file),
            *[str(path) for path in tile_files],
        ],
        check=True,
    )

    if output_file.exists():
        output_file.unlink()

    subprocess.run(
        [
            gdal_translate,
            "-of", "COG",
            "-co", "COMPRESS=ZSTD",
            "-co", "BIGTIFF=IF_SAFER",
            str(vrt_file),
            str(output_file),
        ],
        check=True,
    )

    if not output_file.is_file():
        raise RuntimeError("Final mosaic was not created.")

    # Verify the final mosaic strictly before creating metadata.
    valid_cog, errors, warnings = cog_validate(
        str(output_file),
        strict=True,
    )

    print("\nFINAL MOSAIC COG VALIDATION")
    print("Valid COG:", valid_cog)
    print("Errors:", errors)
    print("Warnings:", warnings)

    if not valid_cog:
        raise RuntimeError(
            f"Final mosaic failed strict COG validation: {errors}"
        )

    # These fields describe the masking policy. The expensive
    # per-observation before/after statistics are deliberately skipped.
    mask_stats = {
        "method": "Sentinel-2 SCL",
        "applied_before_temporal_composite": True,
        "scope": "each spatial tile",
        "retained_scl_classes": [4, 5, 6],
        "excluded_scl_classes": [0, 1, 2, 3, 7, 8, 9, 10, 11],
        "per_observation_counts_computed": False,
    }

    masking_comparison = {
        "processing_mode": "spatially_tiled",
        "tile_count": len(tiles),
        "tile_size_m": tile_size_m,
        "before_after_rgb_previews_generated": False,
        "tile_manifest": str(manifest_file),
    }

    metadata = create_metadata(
        output_path=output_file,
        aoi_shape=aoi_shape,
        input_parameters=input_parameters,
        start_date=start_date,
        end_date=end_date,
        cloud_cover_threshold=cloud_cover_threshold,
        items=items,
        reference_item=reference_item,
        composite_method=composite_method,
        process=process,
        mask_stats=mask_stats,
        masking_comparison=masking_comparison,
        project_root=project_root,
        preview_paths={},
        valid_count_path=None,
        valid_count_created=False,
    )

    metadata["processing"]["mode"] = "spatially_tiled"
    metadata["processing"]["tile_size_m"] = tile_size_m
    metadata["processing"]["tile_count"] = len(tiles)
    metadata["processing"]["tile_manifest"] = str(manifest_file)
    metadata["processing"]["elapsed_seconds"] = round(
        time.perf_counter() - overall_started, 3
    )

    metadata_file = save_metadata(
        metadata,
        output_dir / "metadata.json",
    )

    print("\nTILED PROCESSING COMPLETED")
    print("Product:", process)
    print("Mosaic:", output_file)
    print("Tile manifest:", manifest_file)
    print("Metadata:", metadata_file)

    return {
        "output_file": output_file,
        "metadata_file": metadata_file,
        "manifest_file": manifest_file,
        "tile_count": len(tiles),
        "elapsed_seconds": time.perf_counter() - overall_started,
    }
