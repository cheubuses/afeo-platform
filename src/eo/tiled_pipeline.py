"""Tile-based processing for large Sentinel-2 AOIs.

Features
--------
* Per-tile scene filtering (only scenes whose real footprint touches the tile).
* Deterministic run folder + atomic tile writes -> an interrupted run resumes.
* Optional process-level parallelism (``max_workers > 1``).
* AOI polygon clip applied to the final 2D result, not the full time stack.
* Per-tile valid-observation-count raster, mosaicked like the main product.
* Nodata value and COG predictor taken from the real tile files.
* Per-tile retries; failed tiles are reported and the run can be re-launched.

NOTE: with ``max_workers > 1`` the calling script MUST be guarded by
``if __name__ == "__main__":`` because worker processes are spawned.
"""

import gc
import hashlib
import json
import multiprocessing
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import dask
import numpy as np
import pystac
import rasterio
from odc.stac import configure_s3_access
from rio_cogeo.cogeo import cog_validate
from shapely.geometry import shape

from eo.tiling import build_aoi_tiles, choose_target_crs
from eo.sentinel2 import load_sentinel2_items
from eo.masking import (
    mask_with_scl,
    count_valid_observations,
    normalize_retained_classes,
    excluded_classes_for,
)
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

SPECTRAL_BANDS = ["red", "green", "blue", "nir", "swir16", "swir22"]

COUNT_NODATA = 65535

# GDAL settings that make thousands of public-S3 reads more robust.
GDAL_ENV = {
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIPLEX": "YES",
}

# ------------------------------------------------------------
# Radiometric scaling
# ------------------------------------------------------------

def _scale_tile_from_metadata(data, items, tile_id):
    """Apply each loaded band's consistent STAC scale and offset."""

    for band_name in SPECTRAL_BANDS:
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

            raster_bands = asset.extra_fields.get("raster:bands") or []

            if not raster_bands:
                raise RuntimeError(
                    f"{tile_id}: missing raster:bands metadata "
                    f"for {band_name}, scene {item.id}."
                )

            meta = raster_bands[0]
            scale = meta.get("scale")
            offset = meta.get("offset", 0.0)
            nodata = meta.get("nodata", 0)

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
                f"{tile_id}: inconsistent scale, offset or NoData "
                f"metadata for {band_name}: {parameters}"
            )

        scale, offset, nodata = next(iter(parameters))

        raw = data[band_name]
        valid = raw.notnull() & (raw != nodata)

        data[band_name] = (
            raw.astype("float32") * scale + offset
        ).where(valid)

    return data


# ------------------------------------------------------------
# Single-tile worker (top-level so it can be pickled)
# ------------------------------------------------------------

def _atomic_export_cog(array, final_path):
    partial = final_path.with_name(final_path.stem + ".partial.tif")
    if partial.exists():
        partial.unlink()
    export_cog(array, partial)
    if not partial.is_file():
        raise RuntimeError(f"COG export did not create {partial}.")
    partial.replace(final_path)


def _write_count_raster(count, aoi_geometry, target_crs, final_path):
    """Write the per-pixel valid observation count as uint16 GeoTIFF."""

    if count.rio.crs is None:
        count = count.rio.write_crs(target_crs)

    count = count.astype("float32").rio.clip(
        [aoi_geometry.__geo_interface__],
        crs="EPSG:4326",
        drop=False,
        all_touched=False,
    )

    count = (
        count.fillna(COUNT_NODATA)
        .astype("uint16")
        .rio.write_nodata(COUNT_NODATA, inplace=False)
    )

    partial = final_path.with_name(final_path.stem + ".partial.tif")
    if partial.exists():
        partial.unlink()

    count.rio.to_raster(
        partial,
        driver="GTiff",
        dtype="uint16",
        tiled=True,
        compress="DEFLATE",
    )
    partial.replace(final_path)


def _run_tile(task, items):
    tile = task["tile"]
    tile_id = tile["tile_id"]
    process = task["process"]
    target_crs = task["target_crs"]

    tile_file = Path(task["tile_file"])
    count_file = Path(task["count_file"]) if task["count_file"] else None

    started = time.perf_counter()

    load_started = time.perf_counter()

    data = load_sentinel2_items(
        items=items,
        geometry=None,
        requested_product=process,
        tile_bounds=tile["bounds"],
        target_crs=target_crs,
    )

    load_seconds = time.perf_counter() - load_started

    if data.rio.crs is None:
        raise RuntimeError(f"{tile_id}: loaded data has no CRS.")

    data = _scale_tile_from_metadata(data, items, tile_id)

    masked = mask_with_scl(
        data, retained_classes=task["scl_classes"]
    )

    bands = [b for b in SPECTRAL_BANDS if b in masked.data_vars]

    composite = create_temporal_composite(
        masked[bands],
        method=task["composite_method"],
    )

    result = process_data(composite, process)

    if result is None:
        raise RuntimeError(f"{tile_id}: {process} returned no result.")

    count = None
    if count_file is not None:
        count = count_valid_observations(masked, bands)

    # Compute the shared graph once so the S3 reads are not repeated.
    compute_started = time.perf_counter()

    if count is not None:
        result, count = dask.persist(result, count)
    else:
        (result,) = dask.persist(result)

    compute_seconds = time.perf_counter() - compute_started

    # Clip the final 2D product to the exact AOI polygon.
    if result.rio.crs is None:
        result = result.rio.write_crs(target_crs)

    result = result.rio.clip(
        [tile["aoi_geometry_wgs84"].__geo_interface__],
        crs="EPSG:4326",
        drop=False,
        all_touched=False,
    )

    export_started = time.perf_counter()
    _atomic_export_cog(result, tile_file)
    export_seconds = time.perf_counter() - export_started

    wrote_count = False

    if count is not None:
        _write_count_raster(
            count,
            tile["aoi_geometry_wgs84"],
            target_crs,
            count_file,
        )
        wrote_count = True

    del data, masked, composite, result, count
    gc.collect()

    return {
        "tile_id": tile_id,
        "bounds": list(tile["bounds"]),
        "file": str(tile_file),
        "count_file": str(count_file) if wrote_count else None,
        "scenes_used": len(items),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "export_seconds": round(export_seconds, 3),
        "load_setup_seconds": round(load_seconds, 3),
        "compute_seconds": round(compute_seconds, 3),
        "status": "completed",
    }


def _process_one_tile(task):
    """Process one tile with retries. Never raises; returns a record."""

    tile_id = task["tile"]["tile_id"]
    tile_file = Path(task["tile_file"])
    count_file = Path(task["count_file"]) if task["count_file"] else None

    # Resume: reuse completed outputs.
    if tile_file.is_file() and (count_file is None or count_file.is_file()):
        return {
            "tile_id": tile_id,
            "bounds": list(task["tile"]["bounds"]),
            "file": str(tile_file),
            "count_file": str(count_file) if count_file else None,
            "status": "reused",
        }

    configure_s3_access(aws_unsigned=True)

    items = [pystac.Item.from_dict(d) for d in task["item_dicts"]]

    last_error = None

    for attempt in range(1, task["retries"] + 1):
        try:
            with rasterio.Env(**GDAL_ENV):
                return _run_tile(task, items)
        except Exception as exc:  # noqa: BLE001 - report and retry
            last_error = exc
            for leftover in (tile_file, count_file):
                if leftover is None:
                    continue
                partial = leftover.with_name(leftover.stem + ".partial.tif")
                if partial.exists():
                    partial.unlink()
            time.sleep(2 * attempt)

    return {
        "tile_id": tile_id,
        "bounds": list(task["tile"]["bounds"]),
        "status": "failed",
        "error": f"{type(last_error).__name__}: {last_error}",
    }


# ------------------------------------------------------------
# Helpers for the parent process
# ------------------------------------------------------------

def _run_key(*, process, items, start_date, end_date, composite_method,
             cloud_cover_threshold, tile_size_m, target_crs, aoi_shape,
             scl_classes):
    """Stable hash so re-running identical inputs resumes the same folder."""
    payload = json.dumps(
        {
            "process": process,
            "scenes": sorted(item.id for item in items),
            "start": str(start_date),
            "end": str(end_date),
            "method": composite_method,
            "cloud": float(cloud_cover_threshold),
            "tile_size": tile_size_m,
            "crs": str(target_crs),
            "aoi": aoi_shape.wkb_hex,
            "scl": list(scl_classes),
        },
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:10]


def _write_manifest(path, header, records, status):
    manifest = dict(header)
    manifest["status"] = status
    manifest["tiles"] = records
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2)
    tmp.replace(path)


def _mosaic(files, vrt_file, output_file, *, nodata_arg, cog_options,
            gdalbuildvrt, gdal_translate):
    subprocess.run(
        [
            gdalbuildvrt,
            "-overwrite",
            "-srcnodata", nodata_arg,
            "-vrtnodata", nodata_arg,
            str(vrt_file),
            *[str(path) for path in files],
        ],
        check=True,
    )

    if output_file.exists():
        output_file.unlink()

    subprocess.run(
        [
            gdal_translate,
            "-of", "COG",
            *cog_options,
            str(vrt_file),
            str(output_file),
        ],
        check=True,
    )

    if not output_file.is_file():
        raise RuntimeError(f"Mosaic was not created: {output_file}")


# ------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------

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
    max_workers=1,
    tile_retries=3,
    write_valid_count=True,
    keep_tiles=True,
    scl_retained_classes=None,
):
    """Process a large AOI tile by tile and mosaic the result into a COG."""

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

    max_workers = max(1, int(max_workers))
    scl_classes = normalize_retained_classes(scl_retained_classes)

    output_dir = Path(output_dir)
    project_root = Path(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    gdalbuildvrt = shutil.which("gdalbuildvrt")
    gdal_translate = shutil.which("gdal_translate")

    if not gdalbuildvrt or not gdal_translate:
        raise RuntimeError(
            "gdalbuildvrt and gdal_translate are required. "
            "Install GDAL tools and ensure both are on PATH."
        )

    target_crs = choose_target_crs(items, aoi_shape)

    tiles = build_aoi_tiles(
        aoi_shape,
        tile_size_m=tile_size_m,
        target_crs=target_crs,
    )

    if not tiles:
        raise RuntimeError("No spatial tiles intersect the AOI.")

    # Deterministic folder -> resumable.
    run_key = _run_key(
        process=process,
        items=items,
        start_date=start_date,
        end_date=end_date,
        composite_method=composite_method,
        cloud_cover_threshold=cloud_cover_threshold,
        tile_size_m=tile_size_m,
        target_crs=target_crs,
        aoi_shape=aoi_shape,
        scl_classes=scl_classes,
    )

    run_dir = output_dir / "tiles" / f"{process.lower()}_{run_key}"
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest_file = run_dir / "tile_manifest.json"

    # Real footprints (not bboxes) for per-tile scene selection.
    item_footprints = [
        (item, shape(item.geometry), item.to_dict()) for item in items
    ]

    tasks = []
    skipped = []

    for tile in tiles:
        tile_id = tile["tile_id"]

        selected = [
            (item, d)
            for item, footprint, d in item_footprints
            if footprint.intersects(tile["aoi_geometry_wgs84"])
        ]

        if not selected:
            skipped.append({
                "tile_id": tile_id,
                "bounds": list(tile["bounds"]),
                "status": "skipped_no_scenes",
            })
            continue

        tasks.append({
            "tile": tile,
            "item_dicts": [d for _, d in selected],
            "process": process,
            "composite_method": composite_method,
            "target_crs": target_crs,
            "tile_file": str(run_dir / f"{process.lower()}_{tile_id}.tif"),
            "count_file": (
                str(run_dir / f"validcount_{tile_id}.tif")
                if write_valid_count else None
            ),
            "retries": int(tile_retries),
            "scl_classes": list(scl_classes),
        })

    if not tasks:
        raise RuntimeError("No scenes intersect any tile of the AOI.")

    print("\nTILED PROCESSING")
    print("================")
    print("Product:", process)
    print("Target CRS:", target_crs)
    print("Tile size:", tile_size_m, "metres")
    print("Tiles with data:", len(tasks), "of", len(tiles))
    print("Scenes:", len(items))
    print("Workers:", max_workers)
    print("Run folder:", run_dir)

    header = {
        "product": process,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "composite_method": composite_method,
        "cloud_cover_threshold_percent": float(cloud_cover_threshold),
        "target_crs": str(target_crs),
        "tile_size_m": tile_size_m,
        "scene_ids": [item.id for item in items],
        "run_key": run_key,
    }

    records = list(skipped)
    overall_started = time.perf_counter()
    done = 0

    def _handle(record):
        nonlocal done
        done += 1
        records.append(record)
        print(
            f"[{done}/{len(tasks)}] {record['tile_id']}: "
            f"{record['status']}"
            + (
                f" ({record.get('elapsed_seconds')} s: "
                f"compute {record.get('compute_seconds')} s, "
                f"export {record.get('export_seconds')} s)"
                if record["status"] == "completed" else ""
            )
            + (
                f" - {record['error']}"
                if record["status"] == "failed" else ""
            )
        )
        _write_manifest(manifest_file, header, records, "in_progress")

    if max_workers == 1:
        for task in tasks:
            _handle(_process_one_tile(task))
    else:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=max_workers, mp_context=context
        ) as pool:
            futures = [pool.submit(_process_one_tile, t) for t in tasks]
            for future in as_completed(futures):
                _handle(future.result())

    failed = [r for r in records if r["status"] == "failed"]

    if failed:
        _write_manifest(manifest_file, header, records, "failed")
        raise RuntimeError(
            f"{len(failed)} tile(s) failed. Completed tiles are kept in "
            f"{run_dir}; re-run with identical inputs to resume. "
            f"First error: {failed[0]['error']}"
        )

    _write_manifest(manifest_file, header, records, "tiles_complete")

    tile_files = sorted(
        Path(r["file"]) for r in records if r.get("file")
    )
    count_files = sorted(
        Path(r["count_file"]) for r in records if r.get("count_file")
    )

    # --------------------------------------------------------
    # Mosaic main product
    # --------------------------------------------------------

    with rasterio.open(tile_files[0]) as src:
        tile_nodata = src.nodata
        tile_dtype = np.dtype(src.dtypes[0])

    if tile_nodata is None or np.isnan(tile_nodata):
        nodata_arg = "nan"
    else:
        nodata_arg = str(tile_nodata)

    predictor = "3" if tile_dtype.kind == "f" else "2"

    vrt_file = run_dir / f"{process.lower()}_mosaic.vrt"
    output_file = output_dir / OUTPUT_NAMES[process]

    print("\nBuilding final mosaic...")

    _mosaic(
        tile_files,
        vrt_file,
        output_file,
        nodata_arg=nodata_arg,
        cog_options=[
            "-co", "COMPRESS=ZSTD",
            "-co", f"PREDICTOR={predictor}",
            "-co", "BLOCKSIZE=512",
            "-co", "OVERVIEW_RESAMPLING=AVERAGE",
            "-co", "NUM_THREADS=ALL_CPUS",
            "-co", "BIGTIFF=IF_SAFER",
        ],
        gdalbuildvrt=gdalbuildvrt,
        gdal_translate=gdal_translate,
    )

    valid_cog, errors, warnings = cog_validate(
        str(output_file), strict=True
    )

    print("\nFINAL MOSAIC COG VALIDATION")
    print("Valid COG:", valid_cog)
    print("Errors:", errors)
    print("Warnings:", warnings)

    if not valid_cog:
        raise RuntimeError(
            f"Final mosaic failed strict COG validation: {errors}"
        )

    # --------------------------------------------------------
    # Mosaic valid-observation-count raster
    # --------------------------------------------------------

    valid_count_file = None

    if count_files:
        valid_count_file = output_dir / "valid_observation_count.tif"
        _mosaic(
            count_files,
            run_dir / "validcount_mosaic.vrt",
            valid_count_file,
            nodata_arg=str(COUNT_NODATA),
            cog_options=[
                "-co", "COMPRESS=DEFLATE",
                "-co", "PREDICTOR=2",
                "-co", "NUM_THREADS=ALL_CPUS",
                "-co", "OVERVIEW_RESAMPLING=NEAREST",
                "-co", "BIGTIFF=IF_SAFER",
            ],
            gdalbuildvrt=gdalbuildvrt,
            gdal_translate=gdal_translate,
        )

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    mask_stats = {
        "method": "Sentinel-2 SCL",
        "applied_before_temporal_composite": True,
        "scope": "each spatial tile",
        "retained_scl_classes": list(scl_classes),
        "excluded_scl_classes": excluded_classes_for(scl_classes),
        "per_observation_counts_computed": False,
        "valid_observation_count_raster": (
            str(valid_count_file) if valid_count_file else None
        ),
    }

    masking_comparison = {
        "processing_mode": "spatially_tiled",
        "tile_count": len(tasks),
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
        valid_count_path=valid_count_file,
        valid_count_created=valid_count_file is not None,
    )

    elapsed = time.perf_counter() - overall_started

    metadata["processing"]["mode"] = "spatially_tiled"
    metadata["processing"]["tile_size_m"] = tile_size_m
    metadata["processing"]["tile_count"] = len(tasks)
    metadata["processing"]["tile_manifest"] = str(manifest_file)
    metadata["processing"]["workers"] = max_workers
    metadata["processing"]["elapsed_seconds"] = round(elapsed, 3)

    metadata_file = save_metadata(metadata, output_dir / "metadata.json")

    _write_manifest(manifest_file, header, records, "completed")

    if not keep_tiles:
        shutil.rmtree(run_dir, ignore_errors=True)

    print("\nTILED PROCESSING COMPLETED")
    print("Product:", process)
    print("Mosaic:", output_file)
    print("Metadata:", metadata_file)

    return {
        "output_file": output_file,
        "metadata_file": metadata_file,
        "manifest_file": manifest_file,
        "valid_count_file": valid_count_file,
        "tile_count": len(tasks),
        "elapsed_seconds": elapsed,
    }