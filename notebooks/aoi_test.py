
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
import rasterio
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

from eo.aoi import (
    list_admin1,
    list_admin2,
    list_admin3,
    get_admin_geometry,
)

from eo.sentinel2 import (
    search_sentinel2,
    select_best_scene,
    load_sentinel2_items,
)
from eo.composites import (
    create_temporal_composite,
    save_color_preview,
    save_rgb_preview,
)

from eo.masking import (
    mask_with_scl,
    calculate_masking_statistics,
)
from eo.processor import process_data
from eo.export import export_cog
from eo.metadata import create_metadata, save_metadata


# Create output directory.
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Configure access to public cloud-hosted satellite assets.
configure_s3_access(aws_unsigned=True)



# ============================================================
# 2. MONTHLY DATE AND PROCESSING INPUTS
# ============================================================
# ============================================================
# 2. COLLECT AND VALIDATE AOI INPUTS
# ============================================================

print("\nSelect AOI input method:")
print("1. Administrative area from FieldMaps GeoParquet")
print("2. Latitude, longitude and radius")

aoi_mode = input("Enter choice (1 or 2): ").strip()

# Initialise metadata fields for either AOI method.
latitude = None
longitude = None
radius_km = None

country_code = None
admin_level = None
admin1_name = None
admin2_name = None
admin3_name = None


if aoi_mode == "1":

    country_code = (
        input("Enter country code (default KEN): ").strip().upper()
        or "KEN"
    )

    print("\nSelect administrative level:")
    print("1. Admin 1 (e.g. county)")
    print("2. Admin 2 (e.g. sub-county)")
    print("3. Admin 3 (e.g. ward)")

    admin_level = int(input("Enter level (1, 2 or 3): ").strip())

    if admin_level not in (1, 2, 3):
        raise ValueError("Administrative level must be 1, 2 or 3.")

    # Select Admin 1.
    admin1_options = list_admin1(country_code)

    if not admin1_options:
        raise RuntimeError(
            f"No Admin 1 areas found for {country_code}."
        )

    print("\nAvailable Admin 1 areas:")

    for index, name in enumerate(admin1_options, start=1):
        print(f"{index}. {name}")

    selection = int(input("\nSelect Admin 1 by number: ").strip())

    if not 1 <= selection <= len(admin1_options):
        raise ValueError("Invalid Admin 1 selection.")

    admin1_name = admin1_options[selection - 1]

    # Select Admin 2, if required.
    if admin_level >= 2:

        admin2_options = list_admin2(admin1_name, country_code)

        if not admin2_options:
            raise RuntimeError(
                f"No Admin 2 areas found within {admin1_name}."
            )

        print(f"\nAdmin 2 areas within {admin1_name}:")

        for index, name in enumerate(admin2_options, start=1):
            print(f"{index}. {name}")

        selection = int(input("\nSelect Admin 2 by number: ").strip())

        if not 1 <= selection <= len(admin2_options):
            raise ValueError("Invalid Admin 2 selection.")

        admin2_name = admin2_options[selection - 1]

    # Select Admin 3, if required.
    if admin_level == 3:

        admin3_options = list_admin3(
            admin1_name,
            admin2_name,
            country_code,
        )

        if not admin3_options:
            raise RuntimeError(
                f"No Admin 3 areas found within {admin2_name}."
            )

        print(f"\nAdmin 3 areas within {admin2_name}:")

        for index, name in enumerate(admin3_options, start=1):
            print(f"{index}. {name}")

        selection = int(input("\nSelect Admin 3 by number: ").strip())

        if not 1 <= selection <= len(admin3_options):
            raise ValueError("Invalid Admin 3 selection.")

        admin3_name = admin3_options[selection - 1]

    # Retrieve the selected administrative geometry.
    aoi_shape = get_admin_geometry(
        level=admin_level,
        country_code=country_code,
        admin1_name=admin1_name,
        admin2_name=admin2_name,
        admin3_name=admin3_name,
    )

    if aoi_shape is None or aoi_shape.is_empty:
        raise ValueError("The selected administrative AOI is empty.")

    if not aoi_shape.is_valid:
        raise ValueError("The selected administrative AOI is invalid.")

    geometry = aoi_shape.__geo_interface__

    print("\nSelected administrative AOI:")
    print(f"Country: {country_code}")
    print(f"Admin level: {admin_level}")
    print(f"Admin 1: {admin1_name}")

    if admin2_name:
        print(f"Admin 2: {admin2_name}")

    if admin3_name:
        print(f"Admin 3: {admin3_name}")

    print(f"Geometry type: {aoi_shape.geom_type}")
    print(f"Geometry bounds: {aoi_shape.bounds}")


elif aoi_mode == "2":

    latitude = float(input("Enter latitude: "))
    longitude = float(input("Enter longitude: "))
    radius_km = float(input("Enter processing radius in km: "))

    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")

    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")

    if not math.isfinite(radius_km) or radius_km <= 0:
        raise ValueError(
            "Processing radius must be a finite positive number."
        )

    lat_offset = radius_km / 111.0

    cos_latitude = abs(math.cos(math.radians(latitude)))

    if cos_latitude < 1e-6:
        raise ValueError(
            "Radius-based bounding boxes are unsuitable near the poles."
        )

    lon_offset = radius_km / (111.0 * cos_latitude)

    min_lon = max(-180.0, longitude - lon_offset)
    max_lon = min(180.0, longitude + lon_offset)
    min_lat = max(-90.0, latitude - lat_offset)
    max_lat = min(90.0, latitude + lat_offset)

    aoi_shape = box(min_lon, min_lat, max_lon, max_lat)
    geometry = aoi_shape.__geo_interface__

    print("\nCoordinate-based AOI bounding box:")
    print(f"Longitude: {min_lon:.6f} to {max_lon:.6f}")
    print(f"Latitude:  {min_lat:.6f} to {max_lat:.6f}")

else:
    raise ValueError("Choose AOI input method 1 or 2.")

# ------------------------------------------------------------
# 2A. SELECT YEAR AND MONTH
# ------------------------------------------------------------

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

# ------------------------------------------------------------
# 2B. SELECT TEMPORAL COMPOSITE METHOD
# ------------------------------------------------------------

composite_method = input(
    "Choose composite method (median/mean): "
).strip().lower()

if composite_method not in {"median", "mean"}:
    raise ValueError("Choose either median or mean.")

# ------------------------------------------------------------
# 2C. SET MAXIMUM SCENE CLOUD COVER
# ------------------------------------------------------------

cloud_cover = float(
    input("Enter maximum scene cloud cover (0-100%): ")
)

if not 0 <= cloud_cover <= 100:
    raise ValueError("Cloud cover must be between 0 and 100.")

# ------------------------------------------------------------
# 2D. SELECT THE EO PRODUCT
# ------------------------------------------------------------

print("\nAvailable processing products:")
print("1. RGB          - True-colour composite")
print("2. FALSE_COLOR  - False-colour infrared composite")
print("3. NDVI         - Vegetation index")
print("4. NDWI         - Open-water index")
print("5. NDMI         - Vegetation moisture index")
print("6. NBR          - Normalized Burn Ratio")
print("7. NBR2         - Normalized Burn Ratio 2")
print("8. QUALITY_MASK - Quality mask")

product_options = {
    "1": "RGB",
    "2": "FALSE_COLOR",
    "3": "NDVI",
    "4": "NDWI",
    "5": "NDMI",
    "6": "NBR",
    "7": "NBR2",
    "8": "QUALITY_MASK",
}

choice = (
    input("\nSelect product (enter number or name): ")
    .strip()
    .upper()
    .replace(" ", "_")
    .replace("-", "_")
)

process = product_options.get(choice, choice)

# Accept common alternative names.
aliases = {
    "FALSE_COLOUR": "FALSE_COLOR",
    "FALSE_COLOR_IR": "FALSE_COLOR",
    "NWI": "NDWI",
    "WATER_INDEX": "NDWI",
    "CLOUD_MASK": "QUALITY_MASK",
    "MASK": "QUALITY_MASK",
}

process = aliases.get(process, process)

valid_products = {
    "RGB",
    "FALSE_COLOR",
    "NDVI",
    "NDWI",
    "NDMI",
    "NBR",
    "NBR2",
    "QUALITY_MASK",
}

if process not in valid_products:
    raise ValueError(
        f"Unsupported product: {process}. "
        f"Choose one of: {', '.join(sorted(valid_products))}."
    )

# ------------------------------------------------------------
# 2E. PRINT THE SELECTED INPUTS
# ------------------------------------------------------------

print("\nUser inputs:")
print(f"AOI mode: {aoi_mode}")

if aoi_mode == "1":
    print(f"Country: {country_code}")
    print(f"Administrative level: {admin_level}")
    print(f"Admin 1: {admin1_name}")

    if admin2_name:
        print(f"Admin 2: {admin2_name}")

    if admin3_name:
        print(f"Admin 3: {admin3_name}")
else:
    print(f"Latitude: {latitude}")
    print(f"Longitude: {longitude}")
    print(f"Radius: {radius_km} km")

print(f"Date range: {start_date} to {end_date}")
print(f"Composite method: {composite_method}")
print(f"Maximum scene cloud cover: {cloud_cover}%")
print(f"Requested product: {process}")
# ============================================================
# 3. AOI READY FOR STAC SEARCH
# ============================================================

print("\nAOI geometry prepared for STAC search.")

# ============================================================
# 4. CONNECT TO EARTH SEARCH STAC
# ============================================================

print("\nConnecting to Earth Search STAC...")

catalog = connect_to_catalog()

print("STAC connection successful.")


# ============================================================
# 5A. SEARCH SENTINEL-2 SCENES
# ============================================================

print("\nSearching for Sentinel-2 imagery...")

items = search_sentinel2(
    catalog,
    geometry,
    start_date,
    end_date,
    cloud_cover,
)

# ============================================================
# 5B. DIAGNOSTIC: CHECK SENTINEL-2 OFFSET METADATA
# ============================================================

print("\nCHECKING SENTINEL-2 OFFSET METADATA")

for item in items:
    print("\nScene:", item.id)

    print(
        "Processing baseline:",
        item.properties.get("s2:processing_baseline")
    )

    print(
        "Earth Search BOA offset already applied:",
        item.properties.get("earthsearch:boa_offset_applied")
    )

    for band_name in ["red", "nir"]:
        if band_name in item.assets:
            raster_bands = item.assets[band_name].extra_fields.get(
                "raster:bands", []
            )
            print(f"{band_name.upper()} raster metadata:", raster_bands)

# ============================================================
# 5C. DIAGNOSTIC: INSPECT SOURCE ASSET METADATA
# ============================================================

if not items:
    raise RuntimeError(
        "No Sentinel-2 scenes matched the AOI, date range, "
        "and cloud-cover threshold. Try a wider date range "
        "or a higher cloud-cover threshold."
    )

# Only inspect the first item after confirming items exist.
item = items[0]

print("\nSTAC collection:", item.collection_id)
print("Available assets:", list(item.assets.keys()))

for band_name in ("red", "green", "blue", "nir"):
    asset = item.assets.get(band_name)

    if asset is None:
        print(f"\n{band_name.upper()} asset not found.")
        continue

    raster_bands = asset.extra_fields.get("raster:bands") or []

    print(f"\n{band_name.upper()} asset:")
    print("Raster band metadata:", raster_bands)

    if raster_bands:
        print("Scale:", raster_bands[0].get("scale"))
        print("Offset:", raster_bands[0].get("offset"))
    else:
        print("Scale and offset are not specified in this asset metadata.")

# ============================================================
# DEDUPLICATE ALTERNATE PROCESSING VERSIONS
# ============================================================

def baseline_version(item):
    value = item.properties.get("s2:processing_baseline") or "0"

    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return (0,)


items_by_acquisition = {}

for item in items:
    parts = item.id.split("_")

    # Example:
    # S2B_43QHC_20200226_1_L2A -> S2B_43QHC_20200226
    acquisition_key = (
        "_".join(parts[:3]) if len(parts) >= 3 else item.id
    )

    previous = items_by_acquisition.get(acquisition_key)

    if (
        previous is None
        or baseline_version(item) > baseline_version(previous)
    ):
        items_by_acquisition[acquisition_key] = item

items = sorted(
    items_by_acquisition.values(),
    key=lambda item: item.properties.get("datetime", ""),
)

print(
    f"\nRetained {len(items)} scenes after selecting "
    "the highest processing baseline per acquisition."
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

data = load_sentinel2_items(items, geometry, process)

if data is None:
    raise RuntimeError("Sentinel-2 data loading returned no dataset.")


# ============================================================
# 7A. MASK PIXELS OUTSIDE THE EXACT AOI POLYGON
# ============================================================

print("\nApplying exact AOI polygon mask...")

# Confirm that the AOI geometry is valid.
if aoi_shape.is_empty or not aoi_shape.is_valid:
    raise ValueError("The AOI polygon is empty or invalid.")

# Confirm that the loaded imagery has spatial reference information.
if data.rio.crs is None:
    raise ValueError(
        "Loaded Sentinel-2 data has no CRS; "
        "cannot safely apply the AOI polygon mask."
    )

print("Raster CRS:", data.rio.crs)
print("AOI geometry type:", aoi_shape.geom_type)

# geopolygon limits the extent to the bounding box.
# rio.clip applies the actual polygon boundary.
data = data.rio.clip(
    geometries=[aoi_shape.__geo_interface__],
    crs="EPSG:4326",
    drop=False,
    all_touched=False,
)

print("AOI polygon mask applied.")
print("Raster dimensions:", data.sizes)
print("Pixels outside AOI are set to NoData.")
## ============================================================
# 7B. CONVERT OPTICAL BANDS TO SURFACE REFLECTANCE
#     Using each asset's raster:bands metadata
# ============================================================

SOURCE_NODATA = 0

spectral_bands = [
    "red", "green", "blue", "nir", "swir16", "swir22"
]

print("\nSTAC RADIOMETRIC METADATA AUDIT")
print("=" * 75)

# The BOA flag is printed for diagnostics only.
# It is NOT used to decide whether the offset is applied.
for item in items:
    print(
        f"Scene: {item.id} | "
        f"Baseline: {item.properties.get('s2:processing_baseline')} | "
        f"BOA flag: "
        f"{item.properties.get('earthsearch:boa_offset_applied')}"
    )

print("\nAPPLYING ASSET SCALE AND OFFSET")
print("=" * 75)

for band_name in spectral_bands:

    # Some products do not load every spectral band.
    if band_name not in data.data_vars:
        continue

    source_parameters = []

    # Check the metadata for every scene before transforming
    # the corresponding time-series band.
    for item in items:

        asset = item.assets.get(band_name)

        if asset is None:
            raise RuntimeError(
                f"Asset '{band_name}' is missing from scene "
                f"{item.id}. Cannot verify radiometric scaling."
            )

        raster_bands = (
            asset.extra_fields.get("raster:bands") or []
        )

        if not raster_bands:
            raise RuntimeError(
                f"Missing raster:bands metadata for "
                f"{band_name} in scene {item.id}."
            )

        band_metadata = raster_bands[0]

        scale = band_metadata.get("scale")
        offset = band_metadata.get("offset", 0.0)
        nodata = band_metadata.get("nodata")

        if scale is None:
            raise RuntimeError(
                f"Missing scale for {band_name} "
                f"in scene {item.id}."
            )

        if offset is None:
            offset = 0.0

        if nodata is None:
            nodata = SOURCE_NODATA

        source_parameters.append(
            (float(scale), float(offset), float(nodata))
        )

    # Do not composite a band if the source scenes disagree
    # about the required radiometric transformation.
    distinct_parameters = set(source_parameters)

    if len(distinct_parameters) != 1:
        raise RuntimeError(
            f"Inconsistent scale, offset or NoData metadata "
            f"for band '{band_name}': {distinct_parameters}"
        )

    scale, offset, nodata = next(iter(distinct_parameters))

    # These are the loaded source values, before our transformation.
    raw_band = data[band_name]

    # Preserve NoData and pixels outside the AOI.
    valid_pixels = (
        raw_band.notnull()
        & (raw_band != nodata)
    )

    # Apply the metadata-defined transformation exactly once.
    reflectance = (
        raw_band.astype("float32") * scale + offset
    )

    data[band_name] = reflectance.where(valid_pixels)

    data[band_name].attrs.update({
        "units": "surface reflectance",
        "scale_applied": scale,
        "offset_applied_in_pipeline": offset,
        "source_nodata": nodata,
        "radiometric_transform": "DN * scale + offset",
    })

    print(
        f"{band_name.upper()}: "
        f"scale={scale}, "
        f"offset={offset}, "
        f"NoData={nodata}"
    )

print("\nRadiometric metadata checks passed.")

# ============================================================
# 8. SCL MASKING, VALIDATION AND TEMPORAL COMPOSITE
# ============================================================

print("\nPreparing RGB preview before masking...")

# Confirm that SCL is available before attempting the mask.
if "scl" not in data.data_vars:
    raise ValueError(
        "SCL band is missing. Check that "
        "load_sentinel2_items loads the 'scl' band."
    )

# ------------------------------------------------------------
# 8A. CREATE BEFORE-MASKING RGB PREVIEW
# ------------------------------------------------------------

before_preview_file = (
    OUTPUT_DIR / "rgb_preview_before_masking.png"
)
required_bands = ["red", "green", "blue"]
before_composite = create_temporal_composite(
    data[required_bands],
    method=composite_method,
)

rgb_stretch_limits = save_rgb_preview(
    before_composite,
    before_preview_file,
)

print("Before-masking preview:", before_preview_file)

# ------------------------------------------------------------
# 8B. APPLYING  SCL MASK BEFORE TEMPORAL COMPOSITING
# ------------------------------------------------------------

print("\nValidating SCL alignment and applying cloud/shadow mask...")

original_data = data

# mask_with_scl checks alignment before applying the mask.
masked_data = mask_with_scl(original_data)

# Calculate statistics from the original observations and
# their corresponding masked observations.
mask_stats, valid_observation_count = (
    calculate_masking_statistics(
        original_data,
        masked_data,
    )
)

print("\nSCL MASKING STATISTICS")
print("----------------------")
print(
    "Eligible observations:",
    f"{mask_stats['eligible_observations']:,}",
)
print(
    "Valid observations after masking:",
    f"{mask_stats['valid_observations_after_masking']:,}",
)
print(
    "Observations rejected by SCL:",
    f"{mask_stats['observations_rejected_by_scl']:,}",
)
print(
    "Percentage rejected:",
    f"{mask_stats['masked_percentage']:.2f}%",
)
print("Excluded SCL classes:", mask_stats["excluded_scl_classes"])
print("Retained SCL classes:", mask_stats["retained_scl_classes"])

# ------------------------------------------------------------
# 8C. EXPORT VALID OBSERVATION COUNT PER PIXEL
# ------------------------------------------------------------

valid_count_file = (
    OUTPUT_DIR / "valid_observation_count.tif"
)

if valid_observation_count is not None:
    valid_count_to_save = valid_observation_count

    # Preserve the original grid's georeferencing.
    source_crs = original_data.rio.crs

    if source_crs is not None:
        valid_count_to_save = (
            valid_count_to_save.rio.write_crs(
                source_crs,
                inplace=False,
            )
        )

    valid_count_to_save = (
        valid_count_to_save.rio.write_transform(
            original_data.rio.transform(),
            inplace=False,
        )
    )

    # 65535 is reserved for NoData; zero remains a valid count.
    valid_count_to_save = (
        valid_count_to_save
        .astype("uint16")
        .rio.write_nodata(65535, inplace=False)
    )

    valid_count_to_save.rio.to_raster(
        valid_count_file,
        driver="COG",
        dtype="uint16",
        compress="DEFLATE",
    )

    print(
        "Valid-observation-count COG:",
        valid_count_file,
    )
else:
    print(
        "Valid-observation-count raster not created: "
        "the input has no time dimension."
    )

# ------------------------------------------------------------
# 8D. COMPOSITE THE MASKED SPECTRAL BANDS
# ------------------------------------------------------------

print(
    f"\nCreating masked monthly {composite_method} composite..."
)
# Keep the standard bands plus any SWIR bands loaded for this product.
processing_bands = [
    band
    for band in [
        "red", "green", "blue", "nir", "swir16", "swir22"
    ]
    if band in masked_data.data_vars
]

data = masked_data[processing_bands]

data = create_temporal_composite(
    data,
    method=composite_method,
)
# ------------------------------------------------------------
# 8E. CREATE AFTER-MASKING RGB PREVIEW
# ------------------------------------------------------------

after_preview_file = (
    OUTPUT_DIR / "rgb_preview_after_masking.png"
)

save_rgb_preview(
    data[required_bands],
    after_preview_file,
    stretch_limits=rgb_stretch_limits,
)

print("After-masking preview:", after_preview_file)

# ------------------------------------------------------------
# 8F. VERIFY MASKING GRID AND CALCULATE BEFORE/AFTER STATS
# ------------------------------------------------------------

def get_grid_details(composite):
    """Return spatial metadata for a composite."""
    return {
        "crs": str(composite.rio.crs)
        if composite.rio.crs is not None else None,
        "width": int(composite.rio.width),
        "height": int(composite.rio.height),
        "bounds": [
            float(value) for value in composite.rio.bounds()
        ],
        "resolution": [
            float(value) for value in composite.rio.resolution()
        ],
        "transform": [
            float(value) for value in tuple(composite.rio.transform())
        ],
    }


before_grid = get_grid_details(before_composite)
after_grid = get_grid_details(data)

grid_matches = (
    before_grid["crs"] is not None
    and before_grid["crs"] == after_grid["crs"]
    and before_grid["width"] == after_grid["width"]
    and before_grid["height"] == after_grid["height"]
    and np.allclose(before_grid["bounds"], after_grid["bounds"])
    and np.allclose(
        before_grid["resolution"], after_grid["resolution"]
    )
    and np.allclose(
        before_grid["transform"], after_grid["transform"]
    )
)

print("\nMASKING GRID COMPARISON")
print("======================")
print("Before grid:", before_grid)
print("After grid:", after_grid)
print("Matching CRS, bounds, dimensions and pixel grid:", grid_matches)

if not grid_matches:
    raise RuntimeError(
        "Before- and after-masking composites do not share "
        "the same spatial grid."
    )


# Calculate valid pixels, NoData/non-finite pixels and statistics.
masking_report = {
    "grid_matches": bool(grid_matches),
    "before_grid": before_grid,
    "after_grid": after_grid,
    "same_display_stretch_used": True,
    "previews": {
        "before": str(before_preview_file),
        "after": str(after_preview_file),
    },
    "band_statistics": {},
}

for stage, composite in [
    ("before_masking", before_composite),
    ("after_masking", data),
]:
    masking_report["band_statistics"][stage] = {}

    print(f"\n{stage.upper().replace('_', ' ')}")
    print("-" * 45)

    for band_name in required_bands:
        values = (
            composite[band_name]
            .compute()
            .values
            .astype("float64")
        )

        valid_values = values[np.isfinite(values)]
        total_pixels = int(values.size)
        valid_pixels = int(valid_values.size)
        nodata_pixels = total_pixels - valid_pixels

        stats = {
            "total_pixels": total_pixels,
            "valid_pixels": valid_pixels,
            "nodata_or_nonfinite_pixels": nodata_pixels,
            "minimum": (
                float(valid_values.min())
                if valid_pixels else None
            ),
            "median": (
                float(np.median(valid_values))
                if valid_pixels else None
            ),
            "mean": (
                float(valid_values.mean())
                if valid_pixels else None
            ),
            "maximum": (
                float(valid_values.max())
                if valid_pixels else None
            ),
        }

        masking_report["band_statistics"][stage][band_name] = stats

        print(
            f"{band_name.upper()}: "
            f"valid={valid_pixels:,}, "
            f"NoData/non-finite={nodata_pixels:,}, "
            f"min={stats['minimum']}, "
            f"median={stats['median']}, "
            f"mean={stats['mean']}, "
            f"max={stats['maximum']}"
        )


# Save a reusable report for inspection and metadata integration.
comparison_report_file = OUTPUT_DIR / "masking_comparison_stats.json"

with open(comparison_report_file, "w", encoding="utf-8") as report_file:
    json.dump(masking_report, report_file, indent=2)

print("\nMasking comparison report:", comparison_report_file)
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
# 10. EXPORT THE REQUESTED EO PRODUCT AS A COG
# ============================================================

output_files = {
    "RGB": "rgb.tif",
    "FALSE_COLOR": "false_color.tif",
    "NDVI": "ndvi.tif",
    "NDWI": "ndwi.tif",
    "NDMI": "ndmi.tif",
    "NBR": "nbr.tif",
    "NBR2": "nbr2.tif",
    "QUALITY_MASK": "quality_mask.tif",
}

if process not in output_files:
    raise ValueError(f"Unsupported processing option: {process}")

output_file = OUTPUT_DIR / output_files[process]

print(f"\nExporting {process} product to: {output_file}")

export_cog(result, output_file)

if not output_file.is_file():
    raise RuntimeError(
        f"COG export did not create the expected file: {output_file}"
    )
    

print("COG export completed:", output_file)

# ============================================================
# 10A. REPORT VALID AND NODATA PIXEL COUNTS
# ============================================================


print("\n==========================================")
print("       OUTPUT PIXEL QUALITY REPORT")
print("==========================================")

with rasterio.open(output_file) as src:

    nodata_value = src.nodata
    band_count = src.count
    height = src.height
    width = src.width

    print(f"Output file: {output_file.name}")
    print(f"Raster dimensions: {width} x {height}")
    print(f"Number of bands: {band_count}")
    print(f"NoData value: {nodata_value}")

    total_pixels = height * width

    for band_index in range(1, band_count + 1):

        # Read one band and its validity mask.
        band = src.read(band_index, masked=True)

        valid_mask = (
            ~np.ma.getmaskarray(band)
            & np.isfinite(band.data)
        )

        valid_count = int(np.count_nonzero(valid_mask))
        nodata_count = total_pixels - valid_count

        print(f"\nBand {band_index}:")

        if src.descriptions[band_index - 1]:
            print(f"Band name: {src.descriptions[band_index - 1]}")

        print(f"Valid pixels: {valid_count:,}")
        print(f"NoData pixels: {nodata_count:,}")
        print(
            f"Valid percentage: "
            f"{valid_count / total_pixels * 100:.2f}%"
        )
        print(
            f"NoData percentage: "
            f"{nodata_count / total_pixels * 100:.2f}%"
        )

print("==========================================")

# ============================================================
# 11. CREATE THE REQUESTED PNG PREVIEW
# ============================================================

preview_names = {
    "RGB": "rgb_preview.png",
    "FALSE_COLOR": "false_color_preview.png",
    "NDVI": "ndvi_preview.png",
    "NDWI": "ndwi_preview.png",
    "NDMI": "ndmi_preview.png",
    "NBR": "nbr_preview.png",
    "NBR2": "nbr2_preview.png",
}

preview_file = OUTPUT_DIR / preview_names[process]

if process == "RGB":

    print("Creating reduced-resolution RGB preview...")

    save_rgb_preview(
        result,
        preview_file,
        max_preview_dimension=1600,
    )

    print("Preview saved:", preview_file)

elif process == "FALSE_COLOR":

    print("Creating reduced-resolution false-colour preview...")

    save_color_preview(
        result,
        preview_file,
        band_order=("nir", "red", "green"),
        title="Sentinel-2 false-colour infrared composite",
        max_preview_dimension=1600,
    )

    print("Preview saved:", preview_file)

elif process in {"NDVI", "NDWI", "NDMI", "NBR", "NBR2"}:

    # ------------------------------------------------------------
    # Create a reduced-resolution NDVI preview.
    # The original NDVI result remains full resolution.
    # ------------------------------------------------------------

    max_preview_dimension = 1600

    height = int(result.sizes["y"])
    width = int(result.sizes["x"])

    downsample_factor = max(
        1,
        int(
            np.ceil(
                max(height, width) / max_preview_dimension
            )
        ),
    )

    ndvi_preview_result = result

    if downsample_factor > 1:
        print(
            f"Downsampling NDVI preview by "
            f"{downsample_factor}x for display."
        )

        ndvi_preview_result = result.coarsen(
            y=downsample_factor,
            x=downsample_factor,
            boundary="trim",
        ).mean(skipna=True)

    ndvi_data = ndvi_preview_result.compute()

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
    ax.set_title(f"Sentinel-2 {process} Preview")
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
else:
    raise ValueError(f"Unsupported preview product: {process}")


# ============================================================
# 12. CREATE AND SAVE CURRENT-RUN METADATA
# ============================================================

metadata = create_metadata(
    output_path=output_file,
    aoi_shape=aoi_shape,
    input_parameters={
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
        "country_code": country_code,
        "admin_level": admin_level,
        "admin1_name": admin1_name,
        "admin2_name": admin2_name,
        "admin3_name": admin3_name,
    },
    start_date=start_date,
    end_date=end_date,
    cloud_cover_threshold=cloud_cover,
    items=items,
    reference_item=best_item,
    composite_method=composite_method,
    process=process,
    mask_stats=mask_stats,
    masking_comparison=masking_report,
    project_root=PROJECT_ROOT,
    preview_paths={
        "preview": preview_file,
        "rgb_preview_before_masking": before_preview_file,
        "rgb_preview_after_masking": after_preview_file,
    },
    valid_count_path=valid_count_file,
    valid_count_created=(
        valid_observation_count is not None
        and valid_count_file.is_file()
    ),
)

metadata_file = save_metadata(
    metadata,
    OUTPUT_DIR / "metadata.json",
)


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
