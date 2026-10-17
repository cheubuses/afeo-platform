import json
from pathlib import Path

import numpy as np
import rioxarray
from pystac_client import Client
from shapely.geometry import box

from eo.sentinel2 import search_sentinel2, load_sentinel2_items
from eo.tiling import build_aoi_tiles
from eo.masking import mask_with_scl
from eo.composites import create_temporal_composite
from eo.processor import process_data
from eo.export import export_cog


# --------------------------------------------------
# 1. TEST SETTINGS
# --------------------------------------------------

AOI = box(
    35.981971, 1.981982,
    36.018029, 2.018018,
)

TARGET_CRS = "EPSG:32636"
TILE_SIZE_M = 5000

START_DATE = "2026-02-01"
END_DATE = "2026-02-28"

PRODUCT = "NDMI"
COMPOSITE_METHOD = "median"
CLOUD_LIMIT = 20

OUTPUT_DIR = Path("outputs/tiles")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------
# 2. SEARCH SENTINEL-2 ONCE
# --------------------------------------------------

print("Connecting to Earth Search...")

catalog = Client.open(
    "https://earth-search.aws.element84.com/v1"
)

items = list(
    search_sentinel2(
        catalog=catalog,
        geometry=AOI.__geo_interface__,
        start_date=START_DATE,
        end_date=END_DATE,
        cloud_cover=CLOUD_LIMIT,
    )
)

if not items:
    raise RuntimeError("No Sentinel-2 scenes found.")

print("Scenes found:", len(items))


# --------------------------------------------------
# 3. CREATE THE SPATIAL TILES
# --------------------------------------------------

tiles = build_aoi_tiles(
    AOI,
    tile_size_m=TILE_SIZE_M,
    target_crs=TARGET_CRS,
)

if not tiles:
    raise RuntimeError("No tiles intersect the AOI.")

# Process one tile first.
tile = tiles[0]

print("\nTesting tile:", tile["tile_id"])
print("Tile bounds:", tile["bounds"])
print("Total AOI tiles:", len(tiles))


# --------------------------------------------------
# 4. LOAD THIS TILE ONLY
# --------------------------------------------------

data = load_sentinel2_items(
    items=items,
    geometry=None,
    requested_product=PRODUCT,
    tile_bounds=tile["bounds"],
    target_crs=TARGET_CRS,
)

if data.rio.crs is None:
    raise RuntimeError("Loaded tile has no CRS.")

print("Loaded dimensions:", dict(data.sizes))
print("Loaded bands:", list(data.data_vars))


# --------------------------------------------------
# 5. CLIP TO THE EXACT AOI PORTION OF THIS TILE
# --------------------------------------------------

tile_geometry = tile["aoi_geometry_wgs84"]

data = data.rio.clip(
    geometries=[tile_geometry.__geo_interface__],
    crs="EPSG:4326",
    drop=False,
    all_touched=False,
)

print("Exact AOI clipping completed.")


# --------------------------------------------------
# 6. APPLY STAC SCALE AND OFFSET METADATA
# --------------------------------------------------

spectral_bands = [
    "red", "green", "blue", "nir", "swir16"
]

for band_name in spectral_bands:

    if band_name not in data.data_vars:
        continue

    parameters = set()

    for item in items:

        asset = item.assets.get(band_name)

        if asset is None:
            raise RuntimeError(
                f"Missing {band_name} asset in {item.id}"
            )

        raster_bands = (
            asset.extra_fields.get("raster:bands") or []
        )

        if not raster_bands:
            raise RuntimeError(
                f"Missing raster:bands metadata for "
                f"{band_name} in {item.id}"
            )

        metadata = raster_bands[0]

        if metadata.get("scale") is None:
            raise RuntimeError(
                f"Missing scale metadata for {band_name}"
            )

        scale = float(metadata["scale"])
        offset = float(metadata.get("offset", 0.0))
        nodata = metadata.get("nodata", 0)

        if nodata is None:
            nodata = 0

        parameters.add(
            (scale, offset, float(nodata))
        )

    if len(parameters) != 1:
        raise RuntimeError(
            f"Inconsistent radiometric metadata for "
            f"{band_name}: {parameters}"
        )

    scale, offset, nodata = next(iter(parameters))

    raw = data[band_name]

    valid = raw.notnull() & (raw != nodata)

    reflectance = (
        raw.astype("float32") * scale + offset
    )

    data[band_name] = reflectance.where(valid)

    print(
        f"{band_name}: scale={scale}, "
        f"offset={offset}, NoData={nodata}"
    )


# --------------------------------------------------
# 7. APPLY SCL MASK
# --------------------------------------------------

if "scl" not in data.data_vars:
    raise RuntimeError("SCL band was not loaded.")

masked_data = mask_with_scl(data)

print("SCL masking completed.")


# --------------------------------------------------
# 8. CREATE THE TEMPORAL COMPOSITE
# --------------------------------------------------

processing_bands = [
    name
    for name in [
        "red", "green", "blue", "nir", "swir16"
    ]
    if name in masked_data.data_vars
]

composite = create_temporal_composite(
    masked_data[processing_bands],
    method=COMPOSITE_METHOD,
)

print("Temporal composite completed.")


# --------------------------------------------------
# 9. CALCULATE NDMI
# --------------------------------------------------

result = process_data(composite, PRODUCT)

if result is None:
    raise RuntimeError("NDMI calculation returned no result.")

print("NDMI calculation completed.")


# --------------------------------------------------
# 10. EXPORT THIS TILE AS A COG
# --------------------------------------------------

tile_file = OUTPUT_DIR / f"{PRODUCT.lower()}_{tile['tile_id']}.tif"

export_cog(result, tile_file)

if not tile_file.is_file():
    raise RuntimeError("Tile COG was not created.")

print("Tile COG created:", tile_file)


# --------------------------------------------------
# 11. VERIFY OUTPUT
# --------------------------------------------------

with rioxarray.open_rasterio(tile_file, masked=True) as raster:

    print("\nTILE OUTPUT REPORT")
    print("==================")
    print("File:", tile_file)
    print("Shape:", raster.shape)
    print("CRS:", raster.rio.crs)
    print("Resolution:", raster.rio.resolution())
    print("NoData:", raster.rio.nodata)

    values = raster.squeeze().values.astype("float32")
    valid = values[np.isfinite(values)]

    print("Valid pixels:", int(valid.size))

    if valid.size:
        print("Minimum:", float(valid.min()))
        print("Median:", float(np.median(valid)))
        print("Maximum:", float(valid.max()))

    if valid.size and (
        valid.min() < -1.0001 or valid.max() > 1.0001
    ):
        raise RuntimeError(
            "NDMI values are outside the expected [-1, 1] range."
        )

print("\nPASS: single-tile NDMI smoke test completed.")
print("Note: this test has not yet produced the full-AOI mosaic.")