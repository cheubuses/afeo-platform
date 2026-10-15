
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds, transform_geom
from shapely.geometry import box, mapping


def _json_default(value):
    """Convert common NumPy and Path values to JSON-compatible values."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON.")


def _file_record(path, project_root):
    """Describe an existing output file and calculate its SHA-256 checksum."""
    path = Path(path)

    if not path.is_file():
        return None

    resolved_path = path.resolve()
    root = Path(project_root).resolve()

    try:
        display_path = str(resolved_path.relative_to(root))
    except ValueError:
        display_path = str(resolved_path)

    checksum = hashlib.sha256()

    with resolved_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            checksum.update(chunk)

    return {
        "filename": resolved_path.name,
        "path": display_path,
        "size_bytes": resolved_path.stat().st_size,
        "sha256": checksum.hexdigest(),
    }


def create_metadata(
    *,
    output_path,
    aoi_shape,
    input_parameters,
    start_date,
    end_date,
    cloud_cover_threshold,
    items,
    reference_item,
    composite_method,
    process,
    mask_stats,
    project_root,
    preview_paths=None,
    valid_count_path=None,
    valid_count_created=False,
    masking_comparison=None,
):

    """
    Create a manifest for the current EO processing run.

    Raster properties and valid-pixel statistics are read from
    the actual exported raster, not inferred from the input dataset.
    The input AOI geometry is expected to be in WGS84 (EPSG:4326).
    """
    output_path = Path(output_path)
    project_root = Path(project_root)
    process = process.upper()
    composite_method = composite_method.lower()

    if not output_path.is_file():
        raise FileNotFoundError(
            f"Cannot create metadata: output raster does not exist: "
            f"{output_path}"
        )

    if not items:
        raise ValueError("Cannot create metadata without source scenes.")

    if aoi_shape is None or aoi_shape.is_empty:
        raise ValueError("A valid input AOI geometry is required.")

    # Inspect the exported product using Rasterio.
    with rasterio.open(output_path) as src:
        raster_crs = src.crs

        if raster_crs is None:
            raise ValueError(
                "The exported raster has no CRS; spatial metadata "
                "cannot be validated."
            )

        native_bounds = list(src.bounds)
        geographic_bounds = list(
            transform_bounds(
                raster_crs,
                "EPSG:4326",
                *src.bounds,
                densify_pts=21,
            )
        )

        # Map-ready polygon representing the exported raster footprint.
        footprint_wgs84 = transform_geom(
            raster_crs,
            "EPSG:4326",
            mapping(box(*src.bounds)),
        )

        band_statistics = []

        for band_index in range(1, src.count + 1):
            band = src.read(band_index, masked=True)
            values = np.asarray(band.data)
            valid = (
                ~np.ma.getmaskarray(band)
                & np.isfinite(values)
            )
            valid_values = values[valid]

            stats = {
                "band_index": band_index,
                "name": src.descriptions[band_index - 1],
                "dtype": src.dtypes[band_index - 1],
                "valid_pixels": int(valid_values.size),
                "nodata_pixels": int(src.width * src.height - valid_values.size),
                "valid_percentage": round(
                    float(valid_values.size / (src.width * src.height) * 100),
                    4,
                ),
            }

            if valid_values.size:
                stats.update({
                    "minimum": float(valid_values.min()),
                    "maximum": float(valid_values.max()),
                    "mean": float(valid_values.mean()),
                })
            else:
                stats.update({
                    "minimum": None,
                    "maximum": None,
                    "mean": None,
                })

            band_statistics.append(stats)

        raster_info = {
            "crs": raster_crs.to_string(),
            "epsg": raster_crs.to_epsg(),
            "width": src.width,
            "height": src.height,
            "dimensions": {
                "width": src.width,
                "height": src.height,
            },
            "bounds_native_crs": native_bounds,
            "bounds_wgs84": geographic_bounds,
            "resolution": {
                "x": abs(float(src.transform.a)),
                "y": abs(float(src.transform.e)),
            },
            "transform": list(src.transform),
            "band_count": src.count,
            "data_types": list(src.dtypes),
            "nodata_values": list(src.nodatavals),
            "driver": src.driver,
            "valid_pixel_statistics": band_statistics,
        }

    # Scene-level acquisition details for all scenes supplied to the
    # temporal composite, not just the reference scene.
    scene_records = []

    for item in items:
        properties = item.properties
        scene_records.append({
            "scene_id": item.id,
            "collection": item.collection_id,
            "acquisition_datetime": properties.get("datetime"),
            "cloud_cover_percent": properties.get("eo:cloud_cover"),
            "platform": properties.get("platform"),
            "constellation": properties.get("constellation"),
            "processing_level": properties.get("processing:level"),
        })

    acquisition_dates = sorted({
        record["acquisition_datetime"]
        for record in scene_records
        if record["acquisition_datetime"]
    })

    collections = sorted({
        item.collection_id
        for item in items
        if item.collection_id
    })

    # The input AOI is created from geographic coordinates or an
    # administrative boundary. Its expected input CRS is EPSG:4326.
    aoi_geometry = mapping(aoi_shape)

    # Record the four geographic corners of the AOI bounding box.
    min_lon, min_lat, max_lon, max_lat = aoi_shape.bounds
    aoi_corners = {
        "northwest": [float(min_lon), float(max_lat)],
        "northeast": [float(max_lon), float(max_lat)],
        "southeast": [float(max_lon), float(min_lat)],
        "southwest": [float(min_lon), float(min_lat)],
    }

    cog_record = _file_record(output_path, project_root)

    output_records = {
        "cog": cog_record,
    }

    # Only record previews that exist on disk.
    for label, preview_path in (preview_paths or {}).items():
        if preview_path is None:
            continue

        record = _file_record(preview_path, project_root)

        if record is not None:
            output_records[label] = record

    # Only include the valid-count raster if this run actually created it.
    if valid_count_created and valid_count_path is not None:
        count_record = _file_record(valid_count_path, project_root)

        if count_record is not None:
            output_records["valid_observation_count"] = count_record

    same_month = (
        str(start_date)[:7] == str(end_date)[:7]
    )

    metadata = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "masking_comparison": masking_comparison,
        "input": {
            **input_parameters,
            "aoi_crs": "EPSG:4326",
            "aoi_bbox_wgs84": [
                float(value) for value in aoi_shape.bounds
            ],
            "aoi_geometry_wgs84": aoi_geometry,
            "aoi_bbox_corners_wgs84": aoi_corners,
        },
        "stac_search": {
            "catalog": "https://earth-search.aws.element84.com/v1",
            "collections": collections,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "cloud_cover_threshold_percent": float(
                cloud_cover_threshold
            ),
            "matching_scene_count": len(items),
        },
        "source": {
            "reference_scene_id": reference_item.id,
            "selected_scene_ids": [
                item.id for item in items
            ],
            "acquisition_datetimes": acquisition_dates,
            "scenes": scene_records,
        },
        "temporal_composite": {
            "method": composite_method,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "frequency": "monthly" if same_month else "date_range",
        },
        "masking": dict(mask_stats or {}),
        "spatial": {
            **raster_info,
            "aoi_geometry_crs": "EPSG:4326",
            "raster_footprint_wgs84": footprint_wgs84,
            "raster_footprint_crs": "EPSG:4326",
        },
        "processing": {
            "requested_product": process,
            "requested_products": [process],
            "output_format": "COG",
            "composite_method": composite_method,
            "masking_method": "Sentinel-2 SCL"
                if mask_stats else None,
        },
        "outputs": output_records,
    }

    return metadata


def save_metadata(metadata, output_path):
    """Save the current run's metadata as strict JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            metadata,
            file,
            indent=2,
            allow_nan=False,
            default=_json_default,
        )
        file.write("\n")

    print(f"Metadata saved: {output_path}")
    return output_path
