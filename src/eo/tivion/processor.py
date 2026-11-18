"""AIRSAT-05 SAR product generation for TIVION."""

from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from PIL import Image
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy
from rasterio.warp import transform_bounds


def _sha256(path: Path) -> str:
    """Calculate a file's SHA-256 checksum."""
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(8 * 1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _xml_metadata(path: Path) -> dict[str, Any]:
    """Extract XML leaf values without assuming a namespace."""
    root = ET.parse(path).getroot()
    metadata = {}

    for element in root.iter():
        if len(element) == 0 and element.text:
            value = element.text.strip()

            if value:
                name = element.tag.split("}")[-1]
                metadata.setdefault(name, value)

    return metadata


def _xml_corner_coordinates(path: Path) -> list[list[float]]:
    """Extract vendor footprint corners in GeoJSON polygon order."""

    root = ET.parse(path).getroot()

    def local_name(element):
        return element.tag.split("}")[-1]

    corner_element = next(
        (
            element
            for element in root.iter()
            if local_name(element) == "corner"
        ),
        None,
    )

    if corner_element is None:
        raise ValueError("Vendor XML does not contain a corner element.")

    corner_elements = {
        local_name(element): element
        for element in list(corner_element)
    }

    coordinates = []

    # GeoJSON polygon order: top-left, top-right,
    # bottom-right, bottom-left, and close the ring.
    for corner_name in (
        "topLeft",
        "topRight",
        "bottomRight",
        "bottomLeft",
    ):
        element = corner_elements.get(corner_name)

        if element is None:
            raise ValueError(
                f"Vendor XML is missing the {corner_name} corner."
            )

        values = {
            local_name(child): child.text.strip()
            for child in list(element)
            if child.text and child.text.strip()
        }

        try:
            latitude = float(values["Latitude"])
            longitude = float(values["Longitude"])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Invalid latitude/longitude for {corner_name}."
            ) from exc

        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):
            raise ValueError(
                f"Coordinates out of range for {corner_name}."
            )

        coordinates.append([longitude, latitude])

    coordinates.append(coordinates[0].copy())

    return coordinates



def _create_previews(
    source_path: Path,
    preview_path: Path,
    thumbnail_path: Path,
) -> dict[str, Any]:
    """Create display-only grayscale RGBA PNGs with zero background transparent."""

    with rasterio.open(source_path) as src:
        maximum_dimension = 2400

        scale = min(
            1.0,
            maximum_dimension / max(src.width, src.height),
        )

        width = max(1, round(src.width * scale))
        height = max(1, round(src.height * scale))

        sample = src.read(
            1,
            out_shape=(height, width),
            masked=True,
            resampling=Resampling.average,
        )

        # Nearest-neighbour sampling provides a separate display mask.
        # This is a visualization rule, not a scientific NoData assignment.
        nearest_sample = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.nearest,
        )

    values = np.asarray(sample.compressed(), dtype=np.float64)
    values = values[np.isfinite(values)]

    if values.size == 0:
        raise ValueError("No valid raster values available for preview.")

    stretch_values = values[values != 0]

    if stretch_values.size < 100:
        stretch_values = values

    low, high = np.percentile(stretch_values, [2, 98])

    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low = float(np.min(values))
        high = float(np.max(values))

    if high <= low:
        high = low + 1.0

    display = np.asarray(
        sample.filled(low),
        dtype=np.float64,
    )

    display = np.clip(
        (display - low) / (high - low),
        0,
        1,
    )

    display = (display * 255).round().astype(np.uint8)

    source_mask = np.ma.getmaskarray(sample)

    # Transparent where the nearest source pixel is zero or masked.
    alpha = np.where(
        (nearest_sample != 0) & ~source_mask,
        255,
        0,
    ).astype(np.uint8)

    rgba = np.dstack((display, display, display, alpha))
    image = Image.fromarray(rgba)

    image.save(
        preview_path,
        format="PNG",
        optimize=True,
    )

    thumbnail = image.copy()
    thumbnail.thumbnail(
        (512, 512),
        Image.Resampling.LANCZOS,
    )

    thumbnail.save(
        thumbnail_path,
        format="PNG",
        optimize=True,
    )

    return {
        "method": "2nd-98th percentile linear display stretch",
        "minimum": float(low),
        "maximum": float(high),
        "preview_dimensions": list(image.size),
        "thumbnail_dimensions": list(thumbnail.size),
        "format": "PNG RGBA",
        "transparency": {
            "rule": "Nearest sampled source pixel equal to zero is transparent",
            "alpha_zero": 0,
            "alpha_opaque": 255,
            "purpose": "Display-only background transparency",
            "scientific_nodata_assignment": False,
        },
        "note": (
            "Display-only PNGs with an alpha channel. Zero-valued "
            "pixels are transparent for visualization only. This "
            "does not modify source data or assign scientific NoData."
        ),
    }



def _verify_cog(
    source_path: Path,
    cog_path: Path,
) -> dict[str, Any]:
    """Check COG structure and compare every source pixel."""

    with rasterio.open(source_path) as original, rasterio.open(cog_path) as cog:

        structural_checks = {
            "dimensions_match": (
                original.width == cog.width
                and original.height == cog.height
            ),
            "band_count_matches": original.count == cog.count,
            "crs_matches": original.crs == cog.crs,
            "transform_matches": original.transform == cog.transform,
            "dtype_matches": original.dtypes == cog.dtypes,
            "nodata_matches": original.nodata == cog.nodata,
        }

        if not all(structural_checks.values()):
            raise RuntimeError(
                f"COG structural validation failed: {structural_checks}"
            )

        if cog.driver != "GTiff":
            raise RuntimeError("Output is not a GeoTIFF.")

        pixels_match = True

        # Compare every pixel using block windows.
        for _, window in original.block_windows(1):
            source_data = original.read(1, window=window)
            cog_data = cog.read(1, window=window)

            if not np.array_equal(
                source_data,
                cog_data,
                equal_nan=True,
            ):
                pixels_match = False
                break

        if not pixels_match:
            raise RuntimeError(
                "COG pixel values differ from the original TIFF."
            )

        # Capture all properties before closing the datasets.
        compression = (
            str(cog.compression) if cog.compression else None
        )
        tiled = cog.is_tiled
        block_shapes = [
            list(shape) for shape in cog.block_shapes
        ]
        overview_levels = cog.overviews(1)

    # Only ordinary Python values are accessed here.
    return {
        "structural_checks": structural_checks,
        "pixel_values_identical": True,
        "compression": compression,
        "tiled": tiled,
        "block_shapes": block_shapes,
        "overview_levels": overview_levels,
    }




def _create_map_cog(
    source_path: Path,
    map_cog_path: Path,
) -> dict[str, Any]:
    """Create a visualization COG with an internal zero-value mask.

    The source pixel values are preserved. The internal TIFF mask
    is a provisional display rule, not a scientific NoData assignment.
    """

    temporary_path = map_cog_path.with_name(
        f".{map_cog_path.stem}.mask-source.tif"
    )

    if temporary_path.exists():
        temporary_path.unlink()

    try:
        # Build a tiled GeoTIFF carrying an internal validity mask.
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
            with rasterio.open(source_path) as src:
                profile = src.profile.copy()
                profile.update(
                    driver="GTiff",
                    compress="DEFLATE",
                    tiled=True,
                    blockxsize=512,
                    blockysize=512,
                    BIGTIFF="IF_SAFER",
                )

                with rasterio.open(
                    temporary_path,
                    "w",
                    **profile,
                ) as dst:
                    for _, window in dst.block_windows(1):
                        data = src.read(1, window=window)

                        dst.write(
                            data,
                            1,
                            window=window,
                        )

                        valid_mask = np.where(
                            data != 0,
                            255,
                            0,
                        ).astype(np.uint8)

                        dst.write_mask(
                            valid_mask,
                            window=window,
                        )

            # Convert the masked GeoTIFF into a cloud-optimized GeoTIFF.
            rio_copy(
                temporary_path,
                map_cog_path,
                driver="COG",
                COMPRESS="DEFLATE",
                PREDICTOR="YES",
                BLOCKSIZE=512,
                BIGTIFF="IF_SAFER",
                OVERVIEWS="AUTO",
                RESAMPLING="NEAREST",
                NUM_THREADS="ALL_CPUS",
            )

    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    # Validate georeferencing, pixel values and the internal mask.
    with rasterio.open(source_path) as original, rasterio.open(
        map_cog_path
    ) as mapped:

        structural_checks = {
            "dimensions_match": (
                original.width == mapped.width
                and original.height == mapped.height
            ),
            "band_count_matches": original.count == mapped.count,
            "crs_matches": original.crs == mapped.crs,
            "transform_matches": (
                original.transform == mapped.transform
            ),
            "dtype_matches": original.dtypes == mapped.dtypes,
            "nodata_matches": original.nodata == mapped.nodata,
        }

        if not all(structural_checks.values()):
            raise RuntimeError(
                f"Map COG structural validation failed: "
                f"{structural_checks}"
            )

        if mapped.driver != "GTiff":
            raise RuntimeError("Map output is not a GeoTIFF.")

        pixels_match = True
        mask_matches = True
        transparent_pixels = 0
        opaque_pixels = 0

        for _, window in original.block_windows(1):
            original_data = original.read(
                1,
                window=window,
            )

            mapped_data = mapped.read(
                1,
                window=window,
            )

            mapped_mask = mapped.read_masks(
                1,
                window=window,
            )

            if not np.array_equal(
                original_data,
                mapped_data,
                equal_nan=True,
            ):
                pixels_match = False
                break

            expected_mask = np.where(
                original_data != 0,
                255,
                0,
            ).astype(np.uint8)

            if not np.array_equal(
                expected_mask,
                mapped_mask,
            ):
                mask_matches = False
                break

            transparent_pixels += int(
                np.count_nonzero(mapped_mask == 0)
            )

            opaque_pixels += int(
                np.count_nonzero(mapped_mask == 255)
            )

        if not pixels_match:
            raise RuntimeError(
                "Map COG pixel values differ from the source."
            )

        if not mask_matches:
            raise RuntimeError(
                "Map COG internal mask does not match "
                "the zero-value transparency rule."
            )

    return {
        "created": True,
        "format": "Cloud Optimized GeoTIFF",
        "mask_type": "Internal TIFF validity mask",
        "transparency_rule": "Source pixel value equal to zero is transparent",
        "scientific_nodata_assignment": False,
        "structural_checks": structural_checks,
        "pixel_values_identical": pixels_match,
        "internal_mask_validated": mask_matches,
        "transparent_pixels": transparent_pixels,
        "opaque_pixels": opaque_pixels,
    }


def _tivion_scene_details(xml_path: Path) -> tuple[str, str]:
    """Extract the AIRSAT-05 scene ID and acquisition date from XML."""

    import re

    xml_metadata = _xml_metadata(xml_path)

    # Convert metadata to text so the product ID can be located.
    metadata_text = json.dumps(xml_metadata, default=str)

    product_match = re.search(
        r"AS05_[A-Z0-9]+_[A-Z0-9]+_(\d{6})_",
        metadata_text,
    )

    date_match = re.search(
        r"_(\d{14})_L2_",
        metadata_text,
    )

    if not product_match:
        raise ValueError(
            "Could not extract the six-digit scene ID from XML Productid."
        )

    if not date_match:
        raise ValueError(
            "Could not extract the acquisition timestamp from XML Productid."
        )

    scene_id = f"AS05_{product_match.group(1)}"
    acquisition_date = (
        f"{date_match.group(1)[:4]}-"
        f"{date_match.group(1)[4:6]}-"
        f"{date_match.group(1)[6:8]}"
    )

    return scene_id, acquisition_date

def process_airsat05(
    input_file: str | Path,
    metadata_file: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    
    """
    Generate the six AIRSAT-05 delivery products.

    The source raster is not modified. The COG preserves its stored
    pixel values, dimensions, CRS, affine transform, dtype and NoData.
    """

    source_path = Path(input_file).expanduser().resolve()
    xml_path = Path(metadata_file).expanduser().resolve()

    if not source_path.is_file():
        raise FileNotFoundError(
            f"Input TIFF not found: {source_path}"
        )

    if not xml_path.is_file():
        raise FileNotFoundError(
            f"Metadata XML not found: {xml_path}"
        )

    scene_id, acquisition_date = _tivion_scene_details(xml_path)

    if output_dir is None:
        project_root = Path(__file__).resolve().parents[3]

        root = (
            project_root
            / "outputs"
            / "imagery"
            / "airsat"
            / scene_id
            / acquisition_date
        )
    else:
        root = Path(output_dir).expanduser().resolve()

    source_dir = root / "source"
    cog_dir = root / "cog"
    previews_dir = root / "previews"
    metadata_dir = root / "metadata"

    for folder in (source_dir, cog_dir, previews_dir, metadata_dir):
        folder.mkdir(parents=True, exist_ok=True)

    original_copy = source_dir / "original.tiff"
    xml_copy = source_dir / "original.meta.xml"

    if source_path != original_copy:
        shutil.copy2(source_path, original_copy)

    if xml_path != xml_copy:
        shutil.copy2(xml_path, xml_copy)

    cog_path = cog_dir / "sar_hh_cog.tif"
    map_cog_path = cog_dir / "sar_hh_map_cog.tif"
    preview_path = previews_dir / "sar_hh_preview.png"
    thumbnail_path = previews_dir / "sar_hh_thumbnail.png"

    metadata_path = metadata_dir / "metadata.json"
    footprint_path = metadata_dir / "footprint.geojson"
    manifest_path = root / "manifest.json"

    xml_metadata = _xml_metadata(xml_path)

    with rasterio.open(source_path) as src:
        if src.count != 1:
            raise ValueError(
                f"Expected one HH band; found {src.count} bands."
            )

        if src.crs is None:
            raise ValueError(
                "Source TIFF has no CRS. Cannot create a reliable footprint."
            )

        if src.width <= 0 or src.height <= 0:
            raise ValueError("Invalid source raster dimensions.")

        bounds = src.bounds

        if src.crs.to_epsg() == 4326:
            geographic_bounds = bounds
        else:
            geographic_bounds = transform_bounds(
                src.crs,
                "EPSG:4326",
                *bounds,
                densify_pts=21,
            )

        west, south, east, north = map(
            float,
            geographic_bounds,
        )

        raster_metadata = {
            "filename": source_path.name,
            "width": src.width,
            "height": src.height,
            "band_count": src.count,
            "band_descriptions": list(src.descriptions),
            "data_types": list(src.dtypes),
            "crs": src.crs.to_string(),
            "transform": list(src.transform)[:6],
            "pixel_spacing": [
                float(src.res[0]),
                float(src.res[1]),
            ],
            "native_bounds": {
                "left": float(bounds.left),
                "bottom": float(bounds.bottom),
                "right": float(bounds.right),
                "top": float(bounds.top),
            },
            "geographic_bounds": {
                "west": west,
                "south": south,
                "east": east,
                "north": north,
            },
            "nodata": src.nodata,
        }

    # Reuse and validate an existing COG instead of converting again.
    if cog_path.is_file():
        print(f"Reusing existing COG: {cog_path}")

        cog_validation = _verify_cog(
            source_path,
            cog_path,
        )

        cog_action = "reused_existing"

    else:
        print(f"Creating new COG: {cog_path}")

        rio_copy(
            source_path,
            cog_path,
            driver="COG",
            COMPRESS="DEFLATE",
            PREDICTOR="YES",
            BLOCKSIZE=512,
            BIGTIFF="IF_SAFER",
            OVERVIEWS="AUTO",
            RESAMPLING="AVERAGE",
            NUM_THREADS="ALL_CPUS",
        )

        cog_validation = _verify_cog(
            source_path,
            cog_path,
        )

        cog_action = "created"

    map_cog_validation = _create_map_cog(
        source_path,
        map_cog_path,
    )

    preview_metadata = _create_previews(
        source_path,
        preview_path,
        thumbnail_path,
    )

    # Keep raster extent and vendor scene footprint separate.
    # Neither geometry is automatically used as a NoData mask.

    raster_extent_ring = [
        [west, north],
        [east, north],
        [east, south],
        [west, south],
        [west, north],
    ]

    vendor_footprint_ring = _xml_corner_coordinates(xml_path)

    footprint = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "geometry_role": "raster_extent",
                    "source": "GeoTIFF georeferenced bounds",
                    "crs": "EPSG:4326",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [raster_extent_ring],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "geometry_role": "vendor_scene_footprint",
                    "source": "AIRSAT-05 vendor XML corner coordinates",
                    "crs": "EPSG:4326",
                    "validation_status": "pending_boundary_validation",
                    "used_as_nodata_mask": False,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [vendor_footprint_ring],
                },
            },
        ],
    }

    footprint_path.write_text(
        json.dumps(footprint, indent=2) + "\n"
    )

    generated_time = datetime.now(
        timezone.utc
    ).isoformat()

    metadata = {
        "record_type": "AIRSAT-05 SAR product",
        "generated_utc": generated_time,
        "source": "TIVION",
        "satellite": "AIRSAT-05",
        "polarisation": "HH",
        "product_id": scene_id,
        "acquisition_date": acquisition_date,
        "source_xml_metadata": xml_metadata,
        "asset_paths": {
            "source_original": "source/original.tiff",
            "source_metadata_xml": "source/original.meta.xml",
            "sar_hh_cog": "cog/sar_hh_cog.tif",
            "sar_hh_map_cog": "cog/sar_hh_map_cog.tif",
            "sar_hh_preview": "previews/sar_hh_preview.png",
            "sar_hh_thumbnail": "previews/sar_hh_thumbnail.png",
            "metadata": "metadata/metadata.json",
            "footprint": "metadata/footprint.geojson",
            "manifest": "manifest.json",
        },
        "raster": raster_metadata,
        "cog": cog_validation,
        "map_cog": map_cog_validation,
        "preview": preview_metadata,
        "spatial_resolution": {
            "nominal_resolution_xml_value": (
                float(xml_metadata["NominalResolution"])
                if xml_metadata.get("NominalResolution")
                else None
            ),
            "nominal_resolution_xml_interpretation": (
                "Meaning not independently established. "
                "Do not interpret this value as ground resolution."
            ),
            "vendor_confirmed_nominal_spatial_resolution_m": 0.5,
            "ground_range_resolution_estimated_m": 0.45,
            "azimuth_resolution_estimated_m": 0.62,
            "output_pixel_spacing_m": {
                "width": (
                    float(xml_metadata["Widthspace"])
                    if xml_metadata.get("Widthspace")
                    else None
                ),
                "height": (
                    float(xml_metadata["Heightspace"])
                    if xml_metadata.get("Heightspace")
                    else None
                ),
            },
            "resolution_source": "AIRSAT-05 vendor clarification",
            "pixel_spacing_source": "AIRSAT-05 vendor XML",
        },
        "georeferencing": {
            "product_level": xml_metadata.get("productLevel"),
            "correction_level": "GEC (Geocoded Ellipsoid Corrected)",
            "terrain_correction_applied": False,
            "output_crs": raster_metadata["crs"],
            "description": (
                "Vendor-confirmed systematic geometric correction "
                "with ellipsoid correction; not terrain corrected."
            ),
        },
        "radiometry": {
            "stored_values": (
                "SAR amplitude values, linearly quantized digital numbers"
            ),
            "units": "DN",
            "value_scale": "Linear, not decibels (dB)",
            "calibration_constant_xml": xml_metadata.get("CalibrationConst"),
            "radiometric_model_xml": xml_metadata.get("RadiometricModel"),
            "backscatter_coefficient_calibration": (
                "Not established as calibrated sigma0, gamma0 or beta0."
            ),
            "sigma0_available": False,
            "gamma0_available": False,
            "beta0_available": False,
        },
        "limitations": [
            "The product is L2 GEC (Geocoded Ellipsoid Corrected), "
            "not terrain corrected (GTC).",
            "Stored pixel values are linearly quantized SAR amplitude "
            "digital numbers (DN), not decibels (dB).",
            "Calibrated sigma0, gamma0 and beta0 backscatter coefficients "
            "have not been established for this product.",
            "The scientific COG preserves source raster pixel values "
            "and does not apply additional radiometric calibration.",
            "Zero-valued pixels are retained in the scientific product. "
            "The separate map COG uses an internal display mask that "
            "treats zero-valued pixels as transparent; this is not a "
            "scientific NoData assignment."
        ],
        "footprint_information": {
            "raster_extent_available": True,
            "vendor_scene_footprint_available": True,
            "boundary_validation": "Pending",
            "used_as_nodata_mask": False,
        },
        "scientific_interpretation": {
            "stored_pixel_values": (
                "Original stored digital numbers."
            ),
            "radiometric_calibration": (
                "Stored values are linearly quantized amplitude DN. "
                "Calibration to sigma0, gamma0 or beta0 is not established."
            ),
            "terrain_correction": (
                "Vendor identifies the product as L2 GEC. "
                "Terrain correction has not been applied."
            ),
            "zero_values": (
                "Preserved unchanged. Vendor reports background-filled "
                "zero values outside valid imaging coverage. No NoData "
                "mask is applied pending boundary validation."
            ),
            "footprint": (
                "Raster extent and vendor scene footprint are stored "
                "separately. Boundary validation is pending."
            ),
            "display_values": (
                "RGBA PNGs use a display-only percentile stretch and transparent zero-valued background pixels."
            ),
            "map_cog": (
                "The separate map COG preserves SAR DN values and uses "
                "an internal TIFF mask to make zero-valued pixels "
                "transparent for visualization only. This is not a "
                "scientific NoData assignment."
            ),
        },
    }

    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n"
    )

    assets = {
        "source_original": "source/original.tiff",
        "source_metadata_xml": "source/original.meta.xml",
        "sar_hh_cog": "cog/sar_hh_cog.tif",
        "sar_hh_map_cog": "cog/sar_hh_map_cog.tif",
        "sar_hh_preview": "previews/sar_hh_preview.png",
        "sar_hh_thumbnail": "previews/sar_hh_thumbnail.png",
        "metadata": "metadata/metadata.json",
        "footprint": "metadata/footprint.geojson",
    }

    checksums = {}
    sizes = {}

    for name, relative_path in assets.items():
        asset_path = root / relative_path
        checksums[name] = _sha256(asset_path)
        sizes[name] = asset_path.stat().st_size

    manifest = {
        "manifest_version": "1.0",
        "product_id": scene_id,
        "acquisition_date": acquisition_date,
        "source": "tivion",
        "platform": "AIRSAT-05",
        "polarisation": "HH",
        "assets": assets,
        "asset_sizes_bytes": sizes,
        "checksums_sha256": checksums,
        "generated_utc": generated_time,
    }

    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    return {
        "status": "completed",
        "source": "tivion",
        "product": "SAR_INTENSITY",
        "polarisation": "HH",
        "product_id": scene_id,
        "acquisition_date": acquisition_date,
        "output_directory": str(root),
        "assets": {
            "source_original": str(original_copy),
            "source_metadata_xml": str(xml_copy),
            "sar_hh_cog": str(cog_path),
            "sar_hh_map_cog": str(map_cog_path),
            "sar_hh_preview": str(preview_path),
            "sar_hh_thumbnail": str(thumbnail_path),
            "metadata": str(metadata_path),
            "footprint": str(footprint_path),
            "manifest": str(manifest_path),
        },
        "validation": {
            "cog": cog_validation,
            "map_cog": map_cog_validation,
            "pixel_values_identical": True,
        },
    }
