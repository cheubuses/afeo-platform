# ============================================================
# SENTINEL-2 CLOUD-NATIVE EO PROCESSING
# Coordinate → AOI → STAC → Sentinel-2 → RGB + NDVI → COG
# ============================================================

# ============================================================
# 1. IMPORT LIBRARIES AND CONFIGURE PROJECT PATH
# ============================================================

from pathlib import Path
import sys

# Locate the project root and src directory
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

# Allow Python to find the eo package
sys.path.insert(0, str(SRC_PATH))

# Import project modules only after setting the path
from eo.sentinel2 import search_sentinel2,  select_best_scene, load_sentinel2_items, load_sentinel2
from eo.processor import process_data
from eo.indices import calculate_ndvi
from eo.composites import create_rgb
from eo.metadata import create_metadata, save_metadata
from eo.export import export_cog
from eo.stac import connect_to_catalog

# Other libraries
import math
import numpy as np
import rioxarray
import matplotlib.pyplot as plt
from datetime import date
from shapely.geometry import box
from odc.stac import configure_s3_access, stac_load



# Allowing Python to find the local "eo" package

sys.path.append(
    str(Path(__file__).resolve().parents[1] / "src")
)

# ============================================================
# 2. CONFIGURE AWS ACCESS 

# ============================================================
# Its a fuction provided by odc.stac that configures access to cloud-hosted satellite data.
configure_s3_access(
    aws_unsigned=True
)

# ============================================================
# 3. USER INPUTS
# ============================================================

print("\n==========================================")
print(" Sentinel-2 EO Processing")
print("==========================================\n")

latitude = float(
    input("Enter latitude: ")
)

longitude = float(
    input("Enter longitude: ")
)

radius_km = float(
    input("Enter processing radius in km: ")
)
# Monthly composite date selection
year = int(input("Enter year (e.g. 2026): "))
month = int(input("Enter month (1-12): "))

if month < 1 or month > 12:
    raise ValueError("Month must be between 1 and 12.")

from datetime import date, timedelta

start_date = date(year, month, 1)

if month == 12:
    next_month = date(year + 1, 1, 1)
else:
    next_month = date(year, month + 1, 1)

end_date = next_month - timedelta(days=1)

# Convert dates to strings for the STAC search
start_date = start_date.isoformat()
end_date = end_date.isoformat()

print("Start date:", start_date)
print("End date:", end_date)

# Select compositing method
composite_method = input(
    "Choose composite method (median/mean): "
).strip().lower()

if composite_method not in ["median", "mean"]:
    raise ValueError("Choose median or mean.")
cloud_cover = float(
    input("Enter maximum scene cloud cover (%): ")
)


# Validate the dates
try:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    if start > end:
        raise ValueError(
            "Start date must be before or equal to end date."
        )

except ValueError as error:
    raise ValueError(
        f"Invalid date range: {error}. Use YYYY-MM-DD."
    )



print("\nUser inputs:")
print("Latitude :", latitude)
print("Longitude:", longitude)
print("Radius   :", radius_km, "km")


# ============================================================
# 4. CREATE PROCESSING AREA
# ============================================================

lat_offset = radius_km / 111

lon_offset = radius_km / (
    111 * abs(
        math.cos(
            math.radians(latitude)
        )
    )
)

min_lon = longitude - lon_offset
max_lon = longitude + lon_offset

min_lat = latitude - lat_offset
max_lat = latitude + lat_offset


geometry = box(
    min_lon,
    min_lat,
    max_lon,
    max_lat
).__geo_interface__


print("\nProcessing area:")
print("Minimum longitude:", min_lon)
print("Maximum longitude:", max_lon)
print("Minimum latitude :", min_lat)
print("Maximum latitude :", max_lat)


# ============================================================
# 5. CONNECT TO STAC
# ============================================================

print("\nConnecting to Earth Search STAC...")

catalog = connect_to_catalog()

print("STAC connection successful.")


# ============================================================
# 6. SEARCH SENTINEL-2
# ============================================================

items = search_sentinel2(
    catalog, geometry, start_date, end_date, cloud_cover
)


# ============================================================
# 7. CHECK SEARCH RESULTS
# ============================================================

print("\nAvailable scenes:")

for item in items:
    scene_cloud_cover = item.properties.get(
        "eo:cloud_cover",
        100
    )

    print(
        f"{item.id} | "
        f"Cloud cover: {scene_cloud_cover:.2f}%"
    )

# ============================================================
# 8. SELECT BEST SCENE
# ============================================================


print("\nSelecting best Sentinel-2 scene...")

best_item = select_best_scene(
    items
)

best_cloud_cover = best_item.properties.get(
    "eo:cloud_cover",
    100
)


print("\nBest scene:")
print("Scene ID   :", best_item.id)
print(
    "Cloud cover:",
    f"{best_cloud_cover:.2f}%"
)


print("\n================ METADATA ================")

print("Scene ID:")
print(best_item.id)

print("\nCollection:")
print(best_item.collection_id)

print("\nProperties:")
print(best_item.properties)

print("\nBounding Box:")
print(best_item.bbox)

print("\nGeometry:")
print(best_item.geometry)



# ============================================================
# 9. LOAD MULTIPLE SENTINEL-2 SCENES
# ============================================================

print("\nLoading Sentinel-2 scenes for compositing...")

data = load_sentinel2_items(items, geometry)

print("Sentinel-2 scenes loaded successfully.")
print(data)

# ============================================================
# 10. INSPECT DATA
# ============================================================

print("\nDataset:")
print(data)

print("\nDimensions:")
print(data.dims)

print("\nVariables:")
print(list(data.data_vars))

print("\nCRS:")
print(data.rio.crs)


# ============================================================
# 11. CREATE TEMPORAL MEDIAN COMPOSITE
# ============================================================

print("\nCreating temporal median composite...")


from eo.composites import create_temporal_composite

print(
    f"\nCreating monthly {composite_method} composite..."
)

data = data[
    ["red", "green", "blue", "nir"]
]

data = create_temporal_composite(
    data,
    method=composite_method
)

print("Monthly composite created successfully.")
print(data)


print("Temporal median composite created successfully.")
print(data)


# ============================================================
# 12. CREATE RGB COMPOSITE
# ============================================================

print("\nCreating RGB composite...")

rgb = data[
    [
        "red",
        "green",
        "blue"
    ]
]




# ============================================================
# 13. RUN REQUESTED EO PROCESS
# ============================================================

process = input(
    "\nSelect processing option (RGB or NDVI): "
).strip().upper()

if process not in ["RGB", "NDVI"]:
    raise ValueError(
        "Invalid process. Choose RGB or NDVI."
    )

print(f"\nRunning process: {process}")

result = process_data(
    data,
    process
)

print(f"{process} processing completed successfully.")

print("\nResult:")
print(result)


# ============================================================
# 14. EXPORT REQUESTED PROCESS AS COG
# ============================================================

from pathlib import Path

# Create output directory
OUTPUT_DIR = PROJECT_ROOT / "outputs"
OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

# Define output filename
output_file = OUTPUT_DIR / (
    "rgb.tif" if process == "RGB"
    else "ndvi.tif"
)

print(f"\nExporting {process} as Cloud Optimized GeoTIFF...")

exported_file = export_cog(
    result,
    str(output_file)
)

print("COG export completed successfully.")
print("Output file:", exported_file)



# ============================================================
# 15. GENERATE AND EXPORT RGB PREVIEW PNG
# ============================================================

if process == "RGB":

    print("\nGenerating RGB preview...")

    # Extract the RGB bands from the result
    red = result.sel(band="red") if "band" in result.dims else result["red"]
    green = result.sel(band="green") if "band" in result.dims else result["green"]
    blue = result.sel(band="blue") if "band" in result.dims else result["blue"]

    # Convert bands to NumPy arrays
    red_array = red.compute().values
    green_array = green.compute().values
    blue_array = blue.compute().values

    # Apply percentile stretching for visualization
    def stretch_band(band):
        low, high = np.nanpercentile(band, [2, 98])

        if high <= low:
            return np.zeros(band.shape, dtype=np.uint8)

        stretched = (band - low) / (high - low)
        return np.clip(stretched * 255, 0, 255).astype(np.uint8)

    rgb_image = np.dstack([
        stretch_band(red_array),
        stretch_band(green_array),
        stretch_band(blue_array)
    ])

    # Save the PNG preview
    preview_path = OUTPUT_DIR / "rgb_preview.png"

    plt.figure(figsize=(10, 10))
    plt.imshow(rgb_image)
    plt.axis("off")
    plt.title("Sentinel-2 RGB Composite")
    plt.savefig(
        preview_path,
        dpi=150,
        bbox_inches="tight",
        pad_inches=0
    )
    plt.close()

    print("RGB preview saved:", preview_path)

else:
    preview_path = None
    print("PNG preview skipped: NDVI was selected.")


# ============================================================
# 16. CREATE PROCESSING METADATA
# ============================================================

import json

metadata = {
    # --------------------------------------------------------
    # USER INPUT
    # --------------------------------------------------------
    "input": {
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km
    },

    # --------------------------------------------------------
    # STAC SEARCH
    # --------------------------------------------------------
    "stac_search": {
        "catalog": "Earth Search",
        "collection": "sentinel-2-l2a",
        "date_range": f"{start_date}/{end_date}",
        "start_date": start_date,
        "end_date": end_date,
        "cloud_cover_threshold": cloud_cover
    },

    # --------------------------------------------------------
    # SELECTED SENTINEL-2 SCENE
    # --------------------------------------------------------
    "scene": {
        "scene_id": best_item.id,
        "collection": best_item.collection_id,
        "datetime": best_item.properties.get("datetime"),
        "cloud_cover": best_item.properties.get(
            "eo:cloud_cover"
        ),
        "platform": best_item.properties.get(
            "platform"
        ),
        "constellation": best_item.properties.get(
            "constellation"
        ),
        "processing_level": best_item.properties.get(
            "processing:level"
        )
    },

    # --------------------------------------------------------
    # SPATIAL INFORMATION
    # --------------------------------------------------------
    "spatial": {
        "crs": str(data.rio.crs),
        "epsg": data.rio.crs.to_epsg(),
        "width": data.sizes["x"],
        "height": data.sizes["y"],
        "bbox": best_item.bbox
    },

    # --------------------------------------------------------
    # PROCESSING
    # --------------------------------------------------------
    "processing": {
        "rgb": True,
        "ndvi": True,
        "cloud_native": True,
        "xarray": True,
        "dask": True,
        "output_format": "COG"
    },



    # --------------------------------------------------------
    # OUTPUT FILES
    # --------------------------------------------------------
    "outputs": {
        "cog": (
            "rgb.tif"
            if process == "RGB"
            else "ndvi.tif"
        ),
        "rgb_preview": (
            "rgb_preview.png"
            if process == "RGB"
            else None
        )
    }
}


# ============================================================
# 17. SAVE METADATA JSON
# ============================================================

with open(
    "metadata.json",
    "w"
) as f:

    json.dump(
        metadata,
        f,
        indent=4
    )


print("\nMetadata saved successfully:")
print("metadata.json")