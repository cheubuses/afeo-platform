"""Programmatic entry point for the Sentinel-2 EO processing pipeline."""

from datetime import date
from pathlib import Path
from uuid import uuid4

from shapely.geometry import shape

from odc.stac import configure_s3_access

from eo.stac import connect_to_catalog
from eo.sentinel2 import search_sentinel2, select_best_scene
from eo.tiled_pipeline import process_tiled_aoi, OUTPUT_NAMES


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"


def run_eo_pipeline(
    *,
    aoi_geometry,
    start_date,
    end_date,
    process="NDVI",
    composite_method="median",
    cloud_cover_threshold=20,
    output_dir=None,
    input_parameters=None,
    tile_size_m=5000,
    processing_mode="auto",
    max_window_size_m=10000,
    max_workers=2,
    tile_retries=3,
    write_valid_count=True,
    keep_tiles=False,
    scl_retained_classes=None,
    shared_items=None,
    shared_reference_item=None,
    create_run_directory=True,
):
    """Run the EO pipeline without interactive prompts.

    Parameters
    ----------
    aoi_geometry : shapely geometry or GeoJSON geometry dictionary
        AOI polygon or multipolygon in longitude/latitude (WGS84).
    start_date, end_date : str
        ISO dates, e.g. "2026-02-01".
    process : str
        Product supported by the tiled pipeline, e.g. NDVI or RGB.
    composite_method : str
        Temporal composite method supported by the tiled pipeline.
    cloud_cover_threshold : float
        Maximum scene cloud-cover percentage, from 0 to 100.
    output_dir : path-like, optional
        Output root. A unique subdirectory is created for this run.
    input_parameters : dict, optional
        Additional request context to include in processing metadata.

    Returns
    -------
    dict
        JSON-friendly processing result with product and metadata paths.
    """

    # Validate dates.
    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "start_date and end_date must be valid ISO dates (YYYY-MM-DD)."
        ) from exc

    if start > end:
        raise ValueError("start_date must be on or before end_date.")

    # Validate processing options.
    product = str(process).strip().upper()
    method = str(composite_method).strip().lower()

    if product not in OUTPUT_NAMES:
        raise ValueError(
            f"Unsupported product {product!r}. "
            f"Supported products: {', '.join(OUTPUT_NAMES)}"
        )

    if method not in {"median", "mean"}:
        raise ValueError("composite_method must be 'median' or 'mean'.")

    try:
        cloud_limit = float(cloud_cover_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("cloud_cover_threshold must be numeric.") from exc

    if not 0 <= cloud_limit <= 100:
        raise ValueError("cloud_cover_threshold must be between 0 and 100.")

    # Accept either a Shapely geometry or a GeoJSON geometry dictionary.
    geometry = (
        shape(aoi_geometry)
        if isinstance(aoi_geometry, dict)
        else aoi_geometry
    )

    if geometry is None or geometry.is_empty:
        raise ValueError("AOI geometry must not be empty.")

    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("AOI must be a Polygon or MultiPolygon.")

    if not geometry.is_valid:
        raise ValueError(
            "AOI geometry is invalid. Repair it before processing."
        )

    # Standalone runs retain their UUID folder. Multi-product requests
    # can supply a product-specific folder inside a shared request folder.
    root = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    run_id = uuid4().hex

    if create_run_directory:
        run_output_dir = root / run_id
        run_output_dir.mkdir(parents=True, exist_ok=False)
    else:
        run_output_dir = root
        run_output_dir.mkdir(parents=True, exist_ok=True)

    parameters = dict(input_parameters or {})
    parameters.update(
        {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "process": product,
            "composite_method": method,
            "cloud_cover_threshold": cloud_limit,
            "aoi_type": geometry.geom_type,
            "run_id": run_id,
        }
    )

    # Reuse shared scenes when supplied by a multi-product request.
    if shared_items is not None:
        items = shared_items

        if not items:
            raise RuntimeError("No Sentinel-2 scenes were found.")

        best_item = shared_reference_item

        if best_item is None:
            best_item = select_best_scene(items)

    else:
        # Standalone calls retain their existing catalog-search behavior.
        configure_s3_access(aws_unsigned=True)
        catalog = connect_to_catalog()

        items = search_sentinel2(
            catalog,
            geometry,
            start.isoformat(),
            end.isoformat(),
            cloud_cover=cloud_limit,
        )

        best_item = select_best_scene(items)

    # Reuse the existing tiled processor; do not duplicate its logic.
    result = process_tiled_aoi(
        items=items,
        aoi_shape=geometry,
        process=product,
        composite_method=method,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        cloud_cover_threshold=cloud_limit,
        reference_item=best_item,
        output_dir=run_output_dir,
        project_root=PROJECT_ROOT,
        input_parameters=parameters,
        tile_size_m=tile_size_m,
        processing_mode=processing_mode,
        max_window_size_m=max_window_size_m,
        max_workers=max_workers,
        tile_retries=tile_retries,
        write_valid_count=write_valid_count,
        keep_tiles=keep_tiles,
        scl_retained_classes=scl_retained_classes,
    )

    # Return JSON-friendly values for FastAPI or other Python callers.
    return {
        "status": "completed",
        "run_id": run_id,
        "process": product,
        "composite_method": method,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "cloud_cover_threshold": cloud_limit,
        "aoi_type": geometry.geom_type,
        "scene_count": len(items),
        "scenes": [
            {
                "id": item.id,
                "acquisition_datetime": item.properties.get(
                    "datetime"
                ),
                "cloud_cover": item.properties.get("eo:cloud_cover"),
            }
            for item in items
        ],
        "reference_scene": best_item.id,
        "product_path": str(result["output_file"]),
        "metadata_path": str(result["metadata_file"]),
        "manifest_path": str(result["manifest_file"]),
        "valid_count_path": (
            str(result["valid_count_file"])
            if result.get("valid_count_file") is not None
            else None
        ),
        "processing_mode": result.get("processing_mode", processing_mode),
        "tile_count": int(result["tile_count"]),
        "elapsed_seconds": float(result["elapsed_seconds"]),
    }
