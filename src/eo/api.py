"""Reusable multi-product entry point for the EO processing pipeline."""
from eo.registry import get_source_config
from datetime import date
import json
from math import isfinite
from pathlib import Path
from uuid import uuid4

from shapely.geometry import shape
from odc.stac import configure_s3_access

from eo.pipeline import run_eo_pipeline
from eo.stac import connect_to_catalog
from eo.sentinel2 import search_sentinel2, select_best_scene



def _validate_request(
    aoi,
    start_date,
    end_date,
    max_cloud_cover,
    composite_method,
    products,
    source="sentinel2",
):
    source_config = get_source_config(source)
    source = source_config.name

    if not source_config.implemented:
        raise ValueError(
            f"SOURCE_NOT_IMPLEMENTED: Source {source!r} is registered "
            f"but not implemented. {source_config.description}"
        )

    if not source_config.generic_pipeline:
        raise ValueError(
            f"SOURCE_NOT_SUPPORTED_BY_GENERIC_PIPELINE: "
            f"Source {source!r} has its own processing workflow. "
            f"Use the dedicated endpoint for this source."
        )

    # Accept either a Shapely geometry or a GeoJSON geometry dictionary.
    try:
        geometry = shape(aoi) if isinstance(aoi, dict) else aoi
    except Exception as exc:
        raise ValueError("AOI must be a valid GeoJSON geometry.") from exc

    if geometry is None or geometry.is_empty:
        raise ValueError("AOI geometry must not be empty.")

    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("AOI must be a Polygon or MultiPolygon.")

    if not geometry.is_valid:
        raise ValueError("AOI geometry is invalid.")

    min_x, min_y, max_x, max_y = geometry.bounds

    if (
        min_x < -180 or max_x > 180
        or min_y < -90 or max_y > 90
    ):
        raise ValueError(
            "AOI coordinates must be longitude/latitude in EPSG:4326."
        )

    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Dates must use the YYYY-MM-DD format."
        ) from exc

    if start > end:
        raise ValueError(
            "start_date must be on or before end_date."
        )

    try:
        cloud_limit = float(max_cloud_cover)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "max_cloud_cover must be a number between 0 and 100."
        ) from exc

    if not isfinite(cloud_limit) or not 0 <= cloud_limit <= 100:
        raise ValueError(
            "max_cloud_cover must be between 0 and 100."
        )

    method = str(composite_method).strip().lower()

    if method not in {"median", "mean"}:
        raise ValueError(
            "composite_method must be 'median' or 'mean'."
        )

    if isinstance(products, str):
        products = [products]

    if not isinstance(products, (list, tuple)) or not products:
        raise ValueError(
            "products must be a non-empty list of product names."
        )

    normalized_products = []

    for value in products:
        product = str(value).strip().upper()

        if product not in source_config.products:
            supported = ", ".join(source_config.products)

            raise ValueError(
                f"Unsupported product {product!r} for source "
                f"{source!r}. Supported products: {supported}."
            )

        if product not in normalized_products:
            normalized_products.append(product)

    return (
        geometry,
        start.isoformat(),
        end.isoformat(),
        cloud_limit,
        method,
        normalized_products,
    )


def _portable_request_metadata(value, request_dir):
    """Recursively replace local output paths with request-relative refs."""
    request_dir = Path(request_dir).resolve()

    if isinstance(value, dict):
        return {
            key: _portable_request_metadata(item, request_dir)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _portable_request_metadata(item, request_dir)
            for item in value
        ]
    if isinstance(value, str):
        # Path fields are explicitly identified by their key in the dict
        # traversal below; this string branch also removes the request-root
        # from diagnostic messages such as resumable tile failures.
        return value.replace(str(request_dir), ".")
    return value


def _portable_product_results(product_results, request_dir):
    """Return product metadata with path-valued fields relative to request."""
    path_keys = {
        "product_path", "metadata_path", "manifest_path",
        "valid_count_path", "preview_path", "run_dir",
    }

    def convert(value, key=None):
        if isinstance(value, dict):
            return {k: convert(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, str):
            if key in path_keys:
                try:
                    return Path(value).resolve().relative_to(
                        Path(request_dir).resolve()
                    ).as_posix()
                except (OSError, ValueError):
                    return Path(value).name
            return value.replace(str(Path(request_dir).resolve()), ".")
        return value

    return convert(product_results)


def run_processing(
    *,
    aoi,
    start_date,
    end_date,
    products,
    source="sentinel2",
    max_cloud_cover=20,
    composite_method="median",
    output_dir=None,
    tile_size_m=5000,
    processing_mode="auto",
    max_window_size_m=10000,
    max_workers=2,
    tile_retries=3,
    write_valid_count=True,
    keep_tiles=True,
    scl_retained_classes=None,
):
    """Run one or more EO products and return a JSON-friendly result.

    Parameters
    ----------
    aoi : dict or shapely geometry
        GeoJSON Polygon/MultiPolygon geometry in WGS84.
    start_date, end_date : str
        Dates in YYYY-MM-DD format.
    products : list[str] or str
        Requested products, e.g. ["NDVI", "RGB"].
    max_cloud_cover : float
        Maximum scene cloud cover, from 0 to 100.
    composite_method : str
        "median" or "mean".
    output_dir : path-like, optional
        Root output directory.

    Returns
    -------
    dict
        Request-level status, per-product results and errors.
        Suitable for conversion to a JSON response.
    """

    request_id = uuid4().hex

    try:
        (
            geometry,
            start,
            end,
            cloud_limit,
            method,
            normalized_products,
        ) = _validate_request(
            aoi,
            start_date,
            end_date,
            max_cloud_cover,
            composite_method,
            products,
            source=source,
        )

    except (ValueError, TypeError) as exc:
        message = str(exc)

        if "SOURCE_NOT_IMPLEMENTED:" in message:
            error_code = "SOURCE_NOT_IMPLEMENTED"
            clean_message = message.replace(
                "SOURCE_NOT_IMPLEMENTED:", ""
            ).strip()

        elif "SOURCE_NOT_SUPPORTED_BY_GENERIC_PIPELINE:" in message:
            error_code = "SOURCE_NOT_SUPPORTED_BY_GENERIC_PIPELINE"
            clean_message = message.replace(
                "SOURCE_NOT_SUPPORTED_BY_GENERIC_PIPELINE:", ""
            ).strip()

        else:
            error_code = "INVALID_REQUEST"
            clean_message = message

        return {
            "status": "failed",
            "request_id": request_id,
            "source": str(source).strip().lower(),
            "errors": [
                {
                    "code": error_code,
                    "message": clean_message,
                }
            ],
            "products": {},
        }

    # One readable folder for this complete request.
    output_root = (
        Path(output_dir)
        if output_dir is not None
        else Path(__file__).resolve().parents[2] / "outputs"
    )
    short_id = request_id[:8]
    request_folder_name = (
        f"{start.replace('-', '')}_{end.replace('-', '')}"
        f"_request-{short_id}"
    )
    request_output_dir = output_root / request_folder_name
    request_output_dir.mkdir(parents=True, exist_ok=False)

    request_metadata_path = request_output_dir / "request_metadata.json"

    product_results = {}
    errors = []

    # Search Sentinel-2 once for this validated request.
    try:
        configure_s3_access(aws_unsigned=True)
        catalog = connect_to_catalog()

        shared_items = search_sentinel2(
            catalog,
            geometry,
            start,
            end,
            cloud_cover=cloud_limit,
        )

        shared_reference_item = select_best_scene(shared_items)
        shared_search_error = None

    except Exception as exc:
        shared_items = None
        shared_reference_item = None
        shared_search_error = exc

    for product in normalized_products:
        try:
            # Report a shared search failure for each requested product,
            # preserving the existing per-product response structure.
            if shared_search_error is not None:
                raise shared_search_error

            result = run_eo_pipeline(
                aoi_geometry=geometry,
                start_date=start,
                end_date=end,
                process=product,
                composite_method=method,
                cloud_cover_threshold=cloud_limit,
                output_dir=request_output_dir / product,
                create_run_directory=False,
                tile_size_m=tile_size_m,
                processing_mode=processing_mode,
                max_window_size_m=max_window_size_m,
                max_workers=max_workers,
                tile_retries=tile_retries,
                write_valid_count=write_valid_count,
                keep_tiles=keep_tiles,
                scl_retained_classes=scl_retained_classes,
                shared_items=shared_items,
                shared_reference_item=shared_reference_item,
                input_parameters={
                    "request_id": request_id,
                    "requested_products": normalized_products,
                },
            )
            product_results[product] = {
                "status": "completed",
                **result,
            }

        
        except Exception as exc:
            message = str(exc)

            if "No Sentinel-2 scenes were found" in message:
                error_code = "NO_SCENES_FOUND"
            elif "NO_VALID_PIXELS" in message:
                error_code = "NO_VALID_PIXELS"
            elif "tile(s) failed" in message:
                error_code = "TILE_PROCESSING_FAILED"
            elif "gdalbuildvrt and gdal_translate are required" in message:
                error_code = "GDAL_TOOLS_MISSING"
            else:
                error_code = "PROCESSING_FAILED"

            error = {
                "code": error_code,
                "product": product,
                "message": message,
            }

            # Expose recovery information when tile processing fails.
            if (
                error_code == "TILE_PROCESSING_FAILED"
                and "Completed tiles are kept in " in message
            ):
                run_dir = (
                    message
                    .split("Completed tiles are kept in ", 1)[1]
                    .split(";", 1)[0]
                    .strip()
                    .rstrip(".")
                )

                if run_dir:
                    error["run_dir"] = run_dir
                    error["manifest_path"] = str(
                        Path(run_dir) / "tile_manifest.json"
                    )
                    error["resumable"] = True

            errors.append(error)

            product_results[product] = {
                "status": "failed",
                "product": product,
                "error": error,
            }


    completed_count = sum(
        item["status"] == "completed"
        for item in product_results.values()
    )

    failed_count = len(product_results) - completed_count

    if failed_count == 0:
        overall_status = "completed"
    elif completed_count > 0:
        overall_status = "partial_success"
    else:
        overall_status = "failed"

    scene_counts = [
        item.get("scene_count", 0)
        for item in product_results.values()
        if item["status"] == "completed"
    ]

    response = {
        "status": overall_status,
        "request_id": request_id,
        "request_output_dir": str(request_output_dir),
        "request_metadata_path": str(request_metadata_path),
        "start_date": start,
        "end_date": end,
        "max_cloud_cover": cloud_limit,
        "composite_method": method,
        "source": source,
        "aoi_type": geometry.geom_type,
        "requested_products": normalized_products,
        "completed_products": completed_count,
        "failed_products": failed_count,
        "scene_count": max(scene_counts, default=0),
        "products": product_results,
        "errors": errors,
    }

    request_metadata = {
        "request_id": request_id,
        "status": overall_status,
        "start_date": start,
        "end_date": end,
        "max_cloud_cover": cloud_limit,
        "composite_method": method,
        "source": source,
        "aoi_type": geometry.geom_type,
        "aoi_bounds_wgs84": list(geometry.bounds),
        "requested_products": normalized_products,
        "completed_products": completed_count,
        "failed_products": failed_count,
        "scene_count": max(scene_counts, default=0),
        "products": _portable_product_results(
            product_results, request_output_dir
        ),
        "errors": _portable_request_metadata(errors, request_output_dir),
    }

    request_metadata_path.write_text(
        json.dumps(request_metadata, indent=2),
        encoding="utf-8",
    )

    return response
