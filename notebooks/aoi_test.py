
# ============================================================
# SENTINEL-2 CLOUD-NATIVE EO PROCESSING
# Coordinate -> AOI -> STAC -> Temporal Composite
# -> RGB / NDVI -> COG + Preview + Metadata
# ============================================================


# ============================================================
# 1. IMPORTS AND PROJECT CONFIGURATION
# ============================================================

from pathlib import Path
from datetime import date, timedelta
import sys
import json
import math
import numpy as np
import matplotlib.pyplot as plt
import rioxarray  # Registers the .rio accessor for Xarray
from shapely.geometry import box
from odc.stac import configure_s3_access

# Locate project root and source directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

# Make the local eo package importable.
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

# Project modules.
from eo.stac import connect_to_catalog
from eo.sentinel2 import (
    search_sentinel2,
    select_best_scene,
    load_sentinel2_items,
)
from eo.composites import create_temporal_composite
from eo.processor import process_data
from eo.export import export_cog


# Create output directory.
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Configure access to public cloud-hosted satellite assets.
configure_s3_access(aws_unsigned=True)


# ============================================================
# 2. COLLECT AND VALIDATE USER INPUTS
# ============================================================

print("\n==========================================")
print("       Sentinel-2 EO Processing")
print("==========================================\n")

latitude = float(input("Enter latitude: "))
longitude = float(input("Enter longitude: "))
radius_km = float(input("Enter processing radius in km: "))

if not -90 <= latitude <= 90:
    raise ValueError("Latitude must be between -90 and 90.")

if not -180 <= longitude <= 180:
    raise ValueError("Longitude must be between -180 and 180.")

if radius_km <= 0:
    raise ValueError("Processing radius must be greater than zero.")

# Monthly date selection.
year = int(input("Enter year (e.g. 2026): "))
month = int(input("Enter month (1-12): "))

if not 1 <= month <= 12:
    raise ValueError("Month must be between 1 and 12.")

start = date(year, month, 1)

if month == 12:
    next_month = date(year + 1, 1, 1)
else:
    next_month = date(year, month + 1, 1)

end = next_month - timedelta(days=1)

start_date = start.isoformat()
end_date = end.isoformat()

composite_method = input(
    "Choose composite method (median/mean): "
).strip().lower()

if composite_method not in {"median", "mean"}:
    raise ValueError("Choose either median or mean.")

cloud_cover = float(
    input("Enter maximum scene cloud cover (0-100%): ")
)

if not 0 <= cloud_cover <= 100:
    raise ValueError("Cloud cover must be between 0 and 100.")

process = input(
    "Select processing option (RGB or NDVI): "
).strip().upper()

if process not in {"RGB", "NDVI"}:
    raise ValueError("Choose either RGB or NDVI.")

print("\nUser inputs:")
print(f"Latitude: {latitude}")
print(f"Longitude: {longitude}")
print(f"Radius: {radius_km} km")
print(f"Date range: {start_date} to {end_date}")
print(f"Composite method: {composite_method}")
print(f"Maximum scene cloud cover: {cloud_cover}%")
print(f"Requested product: {process}")


# ============================================================
# 3. CREATE AREA OF INTEREST (AOI)
# ============================================================

lat_offset = radius_km / 111.0

cos_latitude = abs(math.cos(math.radians(latitude)))

if cos_latitude < 1e-6:
    raise ValueError(
        "This radius-based bounding box is unsuitable near the poles."
    )

lon_offset = radius_km / (111.0 * cos_latitude)

min_lon = longitude - lon_offset
max_lon = longitude + lon_offset
min_lat = max(-90.0, latitude - lat_offset)
max_lat = min(90.0, latitude + lat_offset)

# GeoJSON-style geometry for STAC searches.
geometry = box(
    min_lon, min_lat, max_lon, max_lat
).__geo_interface__

print("\nAOI bounding box:")
print(f"Longitude: {min_lon:.6f} to {max_lon:.6f}")
print(f"Latitude:  {min_lat:.6f} to {max_lat:.6f}")


# ============================================================
# 4. CONNECT TO EARTH SEARCH STAC
# ============================================================

print("\nConnecting to Earth Search STAC...")

catalog = connect_to_catalog()

print("STAC connection successful.")


# ============================================================
# 5. SEARCH SENTINEL-2 SCENES
# ============================================================

print("\nSearching for Sentinel-2 imagery...")

items = search_sentinel2(
    catalog,
    geometry,
    start_date,
    end_date,
    cloud_cover,
)

if not items:
    raise RuntimeError(
        "No Sentinel-2 scenes matched the AOI, date range, "
        "and cloud-cover threshold. Try a wider date range "
        "or a higher cloud-cover threshold."
    )

print(f"\nFound {len(items)} scene(s):")

for item in items:
    scene_cloud_cover = item.properties.get("eo:cloud_cover")

    if scene_cloud_cover is None:
        cloud_text = "unknown"
    else:
        cloud_text = f"{scene_cloud_cover:.2f}%"

    print(f"{item.id} | Cloud cover: {cloud_text}")


# ============================================================
# 6. SELECT THE BEST SCENE FOR REFERENCE
# ============================================================

print("\nSelecting the best scene for reference...")

best_item = select_best_scene(items)

if best_item is None:
    raise RuntimeError("Could not select a suitable Sentinel-2 scene.")

best_cloud_cover = best_item.properties.get("eo:cloud_cover")

print(f"Selected scene: {best_item.id}")
print(f"Scene cloud cover: {best_cloud_cover}")


# ============================================================
# 7. LOAD SCENES AND INSPECT DATA
# ============================================================

print("\nLoading Sentinel-2 scenes for compositing...")

data = load_sentinel2_items(items, geometry)

if data is None:
    raise RuntimeError("Sentinel-2 data loading returned no dataset.")

required_bands = ["red", "green", "blue", "nir"]
missing_bands = [
    band for band in required_bands
    if band not in data.data_vars
]

if missing_bands:
    raise ValueError(
        f"Loaded dataset is missing required bands: {missing_bands}"
    )

print("\nLoaded dataset:")
print(data)

print("\nDimensions:", data.dims)
print("Variables:", list(data.data_vars))
print("CRS:", data.rio.crs)


# ============================================================
# 8. CREATE TEMPORAL COMPOSITE
# ============================================================

print(f"\nCreating monthly {composite_method} composite...")

# Keep only the bands needed for RGB and NDVI.
data = data[required_bands]

data = create_temporal_composite(
    data,
    method=composite_method,
)

print("Temporal composite created successfully.")
print(data)


# ============================================================
# 9. PROCESS THE REQUESTED PRODUCT
# ============================================================

print(f"\nRunning {process} processing...")

result = process_data(data, process)

if result is None:
    raise RuntimeError(f"{process} processing returned no result.")

print(f"{process} processing completed successfully.")
print(result)


# ============================================================
# 10. EXPORT THE PRODUCT AS A COG
# ============================================================

output_file = OUTPUT_DIR / (
    "rgb.tif" if process == "RGB" else "ndvi.tif"
)

print(f"\nExporting {process} as a Cloud Optimized GeoTIFF...")

exported_file = export_cog(result, output_file)

print("COG export completed:", exported_file)


# ============================================================
# 11. CREATE THE REQUESTED PNG PREVIEW
# ============================================================

preview_file = OUTPUT_DIR / (
    "rgb_preview.png"
    if process == "RGB"
    else "ndvi_preview.png"
)

if process == "RGB":

    # This assumes process_data(..., "RGB") returns a Dataset
    # with separate red, green and blue variables.
    rgb_data = result.compute()

    bands = [
        np.asarray(rgb_data[band].values, dtype=np.float32).squeeze()
        for band in ["red", "green", "blue"]
    ]

    if any(band.ndim != 2 for band in bands):
        raise ValueError(
            "RGB preview requires 2D red, green and blue bands. "
            "Check the dimensions returned by process_data()."
        )

    if not (bands[0].shape == bands[1].shape == bands[2].shape):
        raise ValueError("RGB bands have different spatial shapes.")

    rgb_array = np.stack(bands, axis=-1)

    # Reject non-finite and negative reflectance values.
    valid_rgb = (
        np.isfinite(rgb_array).all(axis=-1)
        & (rgb_array >= 0).all(axis=-1)
    )

    rgb_display = np.zeros_like(rgb_array, dtype=np.float32)

    for band_index in range(3):
        band = rgb_array[..., band_index]
        valid = valid_rgb & np.isfinite(band)

        if valid.any():
            low, high = np.percentile(band[valid], [2, 98])

            if high > low:
                rgb_display[..., band_index] = np.clip(
                    (band - low) / (high - low),
                    0,
                    1,
                )

    # Transparent pixels indicate invalid data.
    alpha = valid_rgb.astype(np.float32)

    plt.imsave(
        preview_file,
        np.dstack([rgb_display, alpha]),
    )

elif process == "NDVI":

    # Compute only when creating the preview.
    ndvi_data = result.compute()

    ndvi_values = np.asarray(
        ndvi_data.values,
        dtype=np.float32,
    ).squeeze()

    if ndvi_values.ndim != 2:
        raise ValueError(
            "NDVI preview requires a 2D array. "
            "Check the dimensions returned by process_data()."
        )

    # Mask NaN, infinity and values outside the theoretical NDVI range.
    valid_ndvi = (
        np.isfinite(ndvi_values)
        & (ndvi_values >= -1)
        & (ndvi_values <= 1)
    )

    ndvi_masked = np.ma.masked_where(
        ~valid_ndvi,
        ndvi_values,
    )

    cmap = plt.get_cmap("RdYlGn").copy()
    cmap.set_bad((0, 0, 0, 0))

    fig, ax = plt.subplots(figsize=(8, 7))

    image = ax.imshow(
        ndvi_masked,
        cmap=cmap,
        vmin=-1,
        vmax=1,
    )

    fig.colorbar(image, ax=ax, label="NDVI")
    ax.set_title("Sentinel-2 NDVI Preview")
    ax.set_axis_off()

    fig.tight_layout()
    fig.savefig(
        preview_file,
        dpi=150,
        bbox_inches="tight",
        transparent=True,
    )
    plt.close(fig)

print("Preview saved:", preview_file)


# ============================================================
# 12. CREATE PROCESSING METADATA
# ============================================================

crs = data.rio.crs

metadata = {
    "input": {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
    },

    "stac_search": {
        "catalog": "Earth Search",
        "collection": "sentinel-2-l2a",
        "start_date": start_date,
        "end_date": end_date,
        "cloud_cover_threshold": cloud_cover,
        "matching_scene_count": len(items),
    },

    # This is the reference scene selected from the search.
    # The temporal composite may use multiple matching scenes.
    "scene": {
        "reference_scene_id": best_item.id,
        "collection": best_item.collection_id,
        "datetime": best_item.properties.get("datetime"),
        "cloud_cover": best_cloud_cover,
        "platform": best_item.properties.get("platform"),
        "constellation": best_item.properties.get("constellation"),
        "processing_level": best_item.properties.get("processing:level"),
    },

    "spatial": {
        "crs": str(crs) if crs else None,
        "epsg": crs.to_epsg() if crs else None,
        "width": data.sizes.get("x"),
        "height": data.sizes.get("y"),
        "aoi_bbox": [min_lon, min_lat, max_lon, max_lat],
    },

    "processing": {
        "product": process,
        "composite_method": composite_method,
        "composite_period": "monthly",
        "year": year,
        "month": month,
        "rgb": process == "RGB",
        "ndvi": process == "NDVI",
        "cloud_native": True,
        "xarray": True,
        "dask": True,
        "output_format": "COG",
    },

    "outputs": {
        "cog": output_file.name,
        "preview": preview_file.name,
        "rgb_preview": (
            "rgb_preview.png" if process == "RGB" else None
        ),
        "ndvi_preview": (
            "ndvi_preview.png" if process == "NDVI" else None
        ),
    },
}


# ============================================================
# 13. SAVE METADATA JSON
# ============================================================

metadata_file = OUTPUT_DIR / "metadata.json"

with open(metadata_file, "w", encoding="utf-8") as file:
    json.dump(metadata, file, indent=4, allow_nan=False)

print("\nMetadata saved:", metadata_file)


# ============================================================
# 14. SUMMARY3
# ============================================================

print("\n==========================================")
print("       PROCESSING COMPLETED")
print("==========================================")
print(f"Product:  {process}")
print(f"Composite: {composite_method}")
print(f"COG:      {output_file}")
print(f"Preview:  {preview_file}")
print(f"Metadata: {metadata_file}")
print("==========================================")
