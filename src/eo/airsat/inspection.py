"""Metadata-only inspection for AIRSAT-05 SAR products."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import rasterio


def _local_name(tag: str) -> str:
    return tag.split("}")[-1]


def _find_text(root: ET.Element, path: str) -> str | None:
    """Find an XML element using a namespace-independent path."""
    parts = path.strip("/").split("/")
    current = [root]

    for part in parts:
        matches = []
        for element in current:
            for child in element:
                if _local_name(child.tag) == part:
                    matches.append(child)
        current = matches

        if not current:
            return None

    for element in current:
        value = (element.text or "").strip()
        if value:
            return value

    return None


def _find_first_text(root: ET.Element, name: str) -> str | None:
    """Find the first occurrence of a field anywhere in the XML tree."""
    for element in root.iter():
        if _local_name(element.tag) == name:
            value = (element.text or "").strip()
            if value:
                return value
    return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _inspect_xml(metadata_path: Path) -> dict[str, Any]:
    """Extract AIRSAT-05 metadata while preserving footprint corner names."""
    try:
        root = ET.parse(metadata_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML metadata file: {exc}") from exc

    fields = {
        "satellite": "satellite",
        "scene_id": "sceneID",
        "segment_id": "segmentID",
        "sensor": "sensorID",
        "product_level": "productLevel",
        "product_type": "productType",
        "product_format": "productFormat",
        "polarisation": "productPolar",
        "imaging_mode": "imagingMode",
        "orbit_id": "orbitID",
        "orbit_direction": "Direction",
        "look_side": "LookSide",
        "acquisition_start": "start",
        "acquisition_end": "end",
        "centre_time": "CenterTime",
        "product_generation_time": "productGentime",
        "radar_frequency_ghz": "RadarCenterFrequency",
        "wavelength_m": "Lambda",
        "centre_look_angle": "centerLookAngle",
        "incidence_angle_near_range": "incidenceAngleNearRange",
        "incidence_angle_far_range": "incidenceAngleFarRange",
        "width": "Width",
        "height": "Height",
        "image_bits": "Imagebit",
        "earth_model": "EarthModel",
        "projection_model": "ProjectModel",
        "calibration_constant": "CalibrationConst",
        "radiometric_model": "RadiometricModel",
        "dem_model": "DEMModel",
        "speckle_suppression_flag": "Speckle_Suppress_Flag",
        "processor_gain_correction": "ProcessorGainCorrection",
        "elevation_pattern_correction": "ElevationPatternCorrection",
        "azimuth_pattern_correction": "AmuthPatternCorrection",
    }

    metadata = {
        key: _find_first_text(root, xml_path)
        for key, xml_path in fields.items()
    }

    metadata["scene_centre"] = {
        "latitude": _to_float(
            _find_text(root, "imageinfo/center/Latitude")
        ),
        "longitude": _to_float(
            _find_text(root, "imageinfo/center/Longitude")
        ),
    }

    corners = {}

    for name in ("topLeft", "topRight", "bottomLeft", "bottomRight"):
        corners[name] = {
            "latitude": _to_float(
                _find_text(root, f"imageinfo/corner/{name}/Latitude")
            ),
            "longitude": _to_float(
                _find_text(root, f"imageinfo/corner/{name}/Longitude")
            ),
        }

    metadata["footprint_corners"] = corners

    valid_corners = [
        point for point in corners.values()
        if point["latitude"] is not None
        and point["longitude"] is not None
    ]

    if len(valid_corners) == 4:
        metadata["footprint_extent"] = {
            "west": min(p["longitude"] for p in valid_corners),
            "south": min(p["latitude"] for p in valid_corners),
            "east": max(p["longitude"] for p in valid_corners),
            "north": max(p["latitude"] for p in valid_corners),
        }
    else:
        metadata["footprint_extent"] = None

    return metadata


def _inspect_raster(input_path: Path) -> dict[str, Any]:
    """Read raster metadata without loading the full pixel array."""
    try:
        with rasterio.open(input_path) as dataset:
            bounds = dataset.bounds

            return {
                "driver": dataset.driver,
                "width": dataset.width,
                "height": dataset.height,
                "band_count": dataset.count,
                "crs": str(dataset.crs) if dataset.crs else None,
                "bounds": {
                    "west": bounds.left,
                    "south": bounds.bottom,
                    "east": bounds.right,
                    "north": bounds.top,
                },
                "resolution": list(dataset.res),
                "transform": list(dataset.transform)[:6],
                "data_types": list(dataset.dtypes),
                "nodata": dataset.nodata,
                "block_shapes": [
                    list(shape) for shape in dataset.block_shapes
                ],
                "is_tiled": dataset.is_tiled,
                "compression": (
                    dataset.compression.value
                    if dataset.compression
                    else None
                ),
            }
    except rasterio.errors.RasterioError as exc:
        raise ValueError(f"Could not read SAR raster: {exc}") from exc


def inspect_airsat05(
    input_file: str | Path,
    metadata_file: str | Path,
) -> dict[str, Any]:
    """
    Inspect an AIRSAT-05 TIFF and XML metadata.

    This function does not read the complete raster pixel array or
    modify either input file.
    """
    raster_path = Path(input_file).expanduser()
    xml_path = Path(metadata_file).expanduser()

    if not raster_path.exists():
        raise FileNotFoundError(f"Raster file not found: {raster_path}")

    if not raster_path.is_file():
        raise ValueError(f"Raster path is not a file: {raster_path}")

    if not xml_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {xml_path}")

    if not xml_path.is_file():
        raise ValueError(f"Metadata path is not a file: {xml_path}")

    raster = _inspect_raster(raster_path)
    metadata = _inspect_xml(xml_path)

    warnings = []

    if metadata.get("width") and metadata.get("height"):
        xml_width = int(metadata["width"])
        xml_height = int(metadata["height"])

        if (xml_width, xml_height) != (
            raster["width"], raster["height"]
        ):
            warnings.append(
                "TIFF dimensions do not match the XML dimensions."
            )

    if metadata.get("calibration_constant") == "0":
        warnings.append(
            "CalibrationConst is 0. Check vendor documentation before "
            "interpreting pixel values as calibrated backscatter."
        )

    if metadata.get("radiometric_model") == "0":
        warnings.append(
            "RadiometricModel is 0. The XML alone does not establish "
            "that pixel values are calibrated sigma0, gamma0, or beta0."
        )

    if raster["nodata"] is None:
        warnings.append(
            "The TIFF does not declare a NoData value. Do not assume "
            "that zero-valued pixels are NoData."
        )

    return {
        "status": "completed",
        "operation": "inspect",
        "source": "airsat",
        "product": "AIRSAT-05 SAR",
        "files": {
            "raster": raster_path.name,
            "metadata": xml_path.name,
        },
        "raster": raster,
        "metadata": metadata,
        "warnings": warnings,
        "inspection_scope": (
            "Metadata-only inspection. No pixel processing, resampling, "
            "or georeferencing correction was performed."
        ),
    }


def validate_airsat05(
    input_file: str | Path,
    metadata_file: str | Path,
) -> dict[str, Any]:
    """
    Validate AIRSAT-05 TIFF georeferencing against its XML footprint.

    This is a metadata-only operation. It does not modify either input.
    """
    import math

    raster_path = Path(input_file).expanduser()
    xml_path = Path(metadata_file).expanduser()

    if not raster_path.exists():
        raise FileNotFoundError(f"Raster file not found: {raster_path}")
    if not raster_path.is_file():
        raise ValueError(f"Raster path is not a file: {raster_path}")
    if not xml_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {xml_path}")
    if not xml_path.is_file():
        raise ValueError(f"Metadata path is not a file: {xml_path}")

    raster = _inspect_raster(raster_path)
    metadata = _inspect_xml(xml_path)

    checks = []
    warnings = []
    errors = []

    # 1. Check whether the TIFF declares a CRS.
    crs = raster.get("crs")
    checks.append({
        "name": "crs_present",
        "passed": bool(crs),
        "value": crs,
    })
    if not crs:
        errors.append("The TIFF does not declare a coordinate reference system.")

    # 2. Check the TIFF affine transform.
    transform = raster["transform"]
    transform_valid = (
        len(transform) >= 6
        and all(math.isfinite(float(v)) for v in transform[:6])
        and transform[0] != 0
        and transform[4] != 0
    )
    checks.append({
        "name": "affine_transform_valid",
        "passed": transform_valid,
        "value": transform,
    })
    if not transform_valid:
        errors.append("The TIFF affine transform is missing or invalid.")

    # 3. Check TIFF and XML dimensions.
    xml_width = _to_float(metadata.get("width"))
    xml_height = _to_float(metadata.get("height"))

    dimensions_match = (
        xml_width is not None
        and xml_height is not None
        and int(xml_width) == raster["width"]
        and int(xml_height) == raster["height"]
    )
    checks.append({
        "name": "dimensions_match",
        "passed": dimensions_match,
        "tiff": {
            "width": raster["width"],
            "height": raster["height"],
        },
        "xml": {
            "width": xml_width,
            "height": xml_height,
        },
    })
    if not dimensions_match:
        warnings.append("TIFF dimensions do not match XML dimensions.")

    # 4. Compare the TIFF bounds with the XML footprint extent.
    tiff_bounds = raster["bounds"]
    xml_bounds = metadata.get("footprint_extent")

    edge_differences = None
    max_difference_m = None

    if xml_bounds:
        mid_lat = (
            tiff_bounds["south"] + tiff_bounds["north"]
            + xml_bounds["south"] + xml_bounds["north"]
        ) / 4.0

        mid_lon = (
            tiff_bounds["west"] + tiff_bounds["east"]
            + xml_bounds["west"] + xml_bounds["east"]
        ) / 4.0

        metres_per_degree_lat = 110574.0
        metres_per_degree_lon = 111320.0 * math.cos(
            math.radians(mid_lat)
        )

        edge_differences = {
            "west_m": (
                tiff_bounds["west"] - xml_bounds["west"]
            ) * metres_per_degree_lon,
            "east_m": (
                tiff_bounds["east"] - xml_bounds["east"]
            ) * metres_per_degree_lon,
            "south_m": (
                tiff_bounds["south"] - xml_bounds["south"]
            ) * metres_per_degree_lat,
            "north_m": (
                tiff_bounds["north"] - xml_bounds["north"]
            ) * metres_per_degree_lat,
        }

        max_difference_m = max(
            abs(value) for value in edge_differences.values()
        )

        bounds_match = max_difference_m <= 1.0

        checks.append({
            "name": "tiff_bounds_match_xml_footprint",
            "passed": bounds_match,
            "tolerance_m": 1.0,
            "maximum_edge_difference_m": round(max_difference_m, 3),
            "edge_differences_m": {
                key: round(value, 3)
                for key, value in edge_differences.items()
            },
        })

        if not bounds_match:
            warnings.append(
                "TIFF bounds differ from the XML footprint extent. "
                "Review the difference before applying georeferencing correction."
            )
    else:
        checks.append({
            "name": "xml_footprint_available",
            "passed": False,
        })
        errors.append(
            "The XML does not contain all four valid footprint corners."
        )

    # 5. Compare the declared projection model with the TIFF CRS.
    projection_model = metadata.get("projection_model")
    projection_note = None

    if (
        projection_model
        and crs
        and projection_model.upper() == "UTM"
        and "4326" in crs
    ):
        projection_note = (
            "XML declares UTM, but the TIFF declares EPSG:4326. "
            "Confirm the vendor's intended georeferencing convention."
        )
        warnings.append(projection_note)

    checks.append({
        "name": "projection_model_consistency",
        "passed": projection_note is None,
        "xml_projection_model": projection_model,
        "tiff_crs": crs,
        "note": projection_note,
    })

    # 6. Check geographic coordinate bounds.
    geographic_bounds_valid = (
        -180 <= tiff_bounds["west"] <= 180
        and -180 <= tiff_bounds["east"] <= 180
        and -90 <= tiff_bounds["south"] <= 90
        and -90 <= tiff_bounds["north"] <= 90
        and tiff_bounds["west"] < tiff_bounds["east"]
        and tiff_bounds["south"] < tiff_bounds["north"]
    )

    checks.append({
        "name": "geographic_bounds_valid",
        "passed": geographic_bounds_valid,
    })

    if not geographic_bounds_valid:
        warnings.append(
            "TIFF bounds do not look like valid geographic coordinates."
        )

    if errors:
        status = "failed"
    elif warnings:
        status = "warning"
    else:
        status = "passed"

    return {
        "status": status,
        "operation": "validate",
        "source": "airsat",
        "product": "AIRSAT-05 SAR",
        "files": {
            "raster": raster_path.name,
            "metadata": xml_path.name,
        },
        "validation": {
            "checks": checks,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
            "maximum_footprint_edge_difference_m": (
                round(max_difference_m, 3)
                if max_difference_m is not None
                else None
            ),
        },
        "raster": raster,
        "xml_footprint_extent": xml_bounds,
        "validation_scope": (
            "Metadata-only georeferencing validation. "
            "No pixel processing, resampling, reprojection, "
            "or georeferencing correction was performed."
        ),
    }


