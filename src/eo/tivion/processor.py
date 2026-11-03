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


def _create_previews(
    source_path: Path,
    preview_path: Path,
    thumbnail_path: Path,
) -> dict[str, Any]:
    """Create display-only grayscale JPEGs from the SAR raster."""

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

    values = np.asarray(sample.compressed(), dtype=np.float64)
    values = values[np.isfinite(values)]

    if values.size == 0:
        raise ValueError("No valid raster values available for preview.")

    # Zero is not assumed to be NoData.
    # It is excluded only from display-stretch statistics when present.
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

    if np.ma.isMaskedArray(sample):
        display[np.ma.getmaskarray(sample)] = 0

    image = Image.fromarray(display).convert("RGB")

    image.save(
        preview_path,
        format="JPEG",
        quality=92,
        optimize=True,
    )

    thumbnail = image.copy()
    thumbnail.thumbnail(
        (512, 512),
        Image.Resampling.LANCZOS,
    )

    thumbnail.save(
        thumbnail_path,
        format="JPEG",
        quality=88,
        optimize=True,
    )

    return {
        "method": "2nd-98th percentile linear display stretch",
        "minimum": float(low),
        "maximum": float(high),
        "preview_dimensions": list(image.size),
        "thumbnail_dimensions": list(thumbnail.size),
        "note": (
            "Display-only JPEGs. They do not represent calibrated "
            "backscatter values."
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
            / "tivion"
            / scene_id
            / acquisition_date
        )
    else:
        root = Path(output_dir).expanduser().resolve()

    source_dir = root / "source"
    processed_dir = root / "processed"
    metadata_dir = root / "metadata"

    for folder in (source_dir, processed_dir, metadata_dir):
        folder.mkdir(parents=True, exist_ok=True)

    original_copy = source_dir / "original.tiff"
    xml_copy = metadata_dir / "original.meta.xml"

    if source_path != original_copy:
        shutil.copy2(source_path, original_copy)

    if xml_path != xml_copy:
        shutil.copy2(xml_path, xml_copy)

    cog_path = processed_dir / "sar_hh_cog.tif"
    preview_path = processed_dir / "sar_hh_preview.jpg"
    thumbnail_path = processed_dir / "sar_hh_thumbnail.jpg"

    metadata_path = metadata_dir / "metadata.json"
    footprint_path = metadata_dir / "footprint.geojson"
    manifest_path = metadata_dir / "manifest.json"

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

    preview_metadata = _create_previews(
        source_path,
        preview_path,
        thumbnail_path,
    )

    # The footprint is derived from the raster's own georeferenced bounds.
    # XML scene corners are not substituted for raster bounds.
    ring = [
        [west, north],
        [east, north],
        [east, south],
        [west, south],
        [west, north],
    ]

    footprint = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "source": "GeoTIFF georeferenced bounds",
                    "crs": "EPSG:4326",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring],
                },
            }
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
        "acquisition_date": acquisition_date,
        "source_xml_metadata": xml_metadata,
        "raster": raster_metadata,
        "cog": cog_validation,
        "preview": preview_metadata,
        "scientific_interpretation": {
            "stored_pixel_values": (
                "Original stored digital numbers."
            ),
            "radiometric_calibration": "Not independently verified",
            "terrain_correction": "Not independently verified",
            "zero_values": (
                "Preserved. Zero is not assumed to mean NoData."
            ),
            "display_values": (
                "JPEGs use a display-only percentile stretch."
            ),
        },
        "limitations": [
            (
                "L2 product level alone does not establish "
                "radiometric calibration or terrain correction."
            ),
            (
                "Vendor documentation is required to establish "
                "the physical meaning of the stored pixel values."
            ),
            (
                "COG creation is a lossless format conversion, "
                "not additional SAR calibration."
            ),
        ],
    }

    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n"
    )

    assets = {
        "source_original": "source/original.tiff",
        "source_metadata_xml": "metadata/original.meta.xml",
        "sar_hh_cog": "processed/sar_hh_cog.tif",
        "sar_hh_preview": "processed/sar_hh_preview.jpg",
        "sar_hh_thumbnail": "processed/sar_hh_thumbnail.jpg",
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
        "output_directory": str(root),
        "assets": {
            "sar_hh_cog": str(cog_path),
            "sar_hh_preview": str(preview_path),
            "sar_hh_thumbnail": str(thumbnail_path),
            "metadata": str(metadata_path),
            "footprint": str(footprint_path),
            "manifest": str(manifest_path),
        },
        "validation": {
            "cog": cog_validation,
            "pixel_values_identical": True,
        },
    }
