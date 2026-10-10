# ============================================================
# SENTINEL-2 CLOUD-NATIVE EO PROCESSING
# Coordinate → AOI → STAC → Sentinel-2 → RGB + NDVI → COG
# ============================================================

# ============================================================
# 1. IMPORT LIBRARIES
# ============================================================

from pathlib import Path
import sys
import math
import numpy as np
import rioxarray
import matplotlib.pyplot as plt
from datetime import date



from pathlib import Path
import sys

# Allow Python to find the local "eo" package
sys.path.append(
    str(Path(__file__).resolve().parents[1] / "src")
)
from eo.processor import process_data
from eo.indices import calculate_ndvi
from eo.composites import create_rgb


from eo.sentinel2 import (
    search_sentinel2,
    select_best_scene,
    load_sentinel2
)

from eo.export import export_cog
# Allowing Python to find the local "eo" package

sys.path.append(
    str(Path(__file__).resolve().parents[1] / "src")
)

from shapely.geometry import box



from odc.stac import (
    stac_load,
    configure_s3_access
)

from eo.stac import connect_to_catalog


# ============================================================
# 2. CONFIGURE AWS ACCESS 
# Its a fuction provided by odc.stac that configures access to cloud-hosted satellite data.
# ============================================================

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
start_date = input(
    "Enter start date (YYYY-MM-DD): "
).strip()

end_date = input(
    "Enter end date (YYYY-MM-DD): "
).strip()


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

print("\nSearching for Sentinel-2 imagery...")

items = search_sentinel2(
    catalog,
    geometry,
    start_date,
    end_date,
    30
)

print(
    f"Number of scenes found: {len(items)}"
)

# ============================================================
# 7. CHECK SEARCH RESULTS
# ============================================================

if len(items) == 0:

    raise RuntimeError(
        "No Sentinel-2 scenes were found for "
        "the selected location and date range."
    )


print("\nAvailable scenes:")

for item in items:

    cloud_cover = item.properties.get(
        "eo:cloud_cover",
        100
    )

    print(
        f"{item.id} | "
        f"Cloud cover: {cloud_cover:.2f}%"
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
# 9. LOAD SENTINEL-2 BANDS
# ============================================================

print("\nLoading Sentinel-2 imagery...")

data = load_sentinel2(
    best_item,
    geometry
)

print("Sentinel-2 data loaded successfully.")


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
# 11. SELECT FIRST TIME SLICE
# ============================================================

data = data.isel(time=0)


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

print(f"\nRunning process: {process}")

result = process_data(
    data,
    process
)

if process == "RGB":
    rgb_stack = result
    print("RGB composite created successfully.")

elif process == "NDVI":
    ndvi = result
    print("NDVI calculated successfully.")


# ============================================================
# 14. EXPORT REQUESTED PROCESS AS COG
# ============================================================

if process == "RGB":

    print("\nExporting RGB as COG...")

    export_cog(
        rgb_stack,
        "rgb.tif"
    )

    print("RGB COG saved as rgb.tif")


elif process == "NDVI":

    print("\nExporting NDVI as COG...")

    export_cog(
        ndvi,
        "ndvi.tif"
    )

    print("NDVI COG saved as ndvi.tif")

    print("RGB preview saved as rgb_preview.png")

else:
    print("\nRGB preview skipped for NDVI.")

# ============================================================
# 15. CREATE PROCESSING METADATA
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
        "cloud_cover_threshold": 30
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
# 20. SAVE METADATA JSON
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