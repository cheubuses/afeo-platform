
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
# 2. COLLECT AND VALIDATE USER INPUTS
# ============================================================

print("\n==========================================")
print("       Sentinel-2 EO Processing")
print("==========================================\n")

print("Select AOI input method:")
print("1. Administrative area from FieldMaps GeoParquet")
print("2. Latitude, longitude and radius")

aoi_mode = input("Enter choice (1 or 2): ").strip()

# Metadata fields: populated according to the selected AOI method.
latitude = None
longitude = None
radius_km = None

country_code = None
admin_level = None
admin1_name = None
admin2_name = None
admin3_name = None


# Keep your existing stac_search, scene, spatial,
# processing and outputs entries below.


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

    # --------------------------------------------------------
    # Select Admin 1
    # --------------------------------------------------------

    admin1_options = list_admin1(country_code)

    if not admin1_options:
        raise RuntimeError(
            f"No Admin 1 areas found for {country_code}."
        )

    print("\nAvailable Admin 1 areas:")

    for index, name in enumerate(admin1_options, start=1):
        print(f"{index}. {name}")

    selection = int(
        input("\nSelect Admin 1 by number: ").strip()
    )

    if not 1 <= selection <= len(admin1_options):
        raise ValueError("Invalid Admin 1 selection.")

    admin1_name = admin1_options[selection - 1]

    # --------------------------------------------------------
    # Select Admin 2, if requested
    # --------------------------------------------------------

    if admin_level >= 2:

        admin2_options = list_admin2(
            admin1_name,
            country_code,
        )

        if not admin2_options:
            raise RuntimeError(
                f"No Admin 2 areas found within {admin1_name}."
            )

        print(f"\nAdmin 2 areas within {admin1_name}:")

        for index, name in enumerate(admin2_options, start=1):
            print(f"{index}. {name}")

        selection = int(
            input("\nSelect Admin 2 by number: ").strip()
        )

        if not 1 <= selection <= len(admin2_options):
            raise ValueError("Invalid Admin 2 selection.")

        admin2_name = admin2_options[selection - 1]

    # --------------------------------------------------------
    # Select Admin 3, if requested
    # --------------------------------------------------------

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

        selection = int(
            input("\nSelect Admin 3 by number: ").strip()
        )

        if not 1 <= selection <= len(admin3_options):
            raise ValueError("Invalid Admin 3 selection.")

        admin3_name = admin3_options[selection - 1]

    # --------------------------------------------------------
    # Retrieve the selected administrative geometry
    # --------------------------------------------------------

    aoi_shape = get_admin_geometry(
        level=admin_level,
        country_code=country_code,
        admin1_name=admin1_name,
        admin2_name=admin2_name,
        admin3_name=admin3_name,
    )

    # Convert the Shapely geometry to GeoJSON-style mapping
    # for the existing STAC search workflow.
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
    print(f"Geometry valid: {aoi_shape.is_valid}")
    print(f"Geometry bounds: {aoi_shape.bounds}")

elif aoi_mode == "2":

    latitude = float(input("Enter latitude: "))
    longitude = float(input("Enter longitude: "))
    radius_km = float(
        input("Enter processing radius in km: ")
    )

    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")

    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")

    if radius_km <= 0:
        raise ValueError(
            "Processing radius must be greater than zero."
        )

    lat_offset = radius_km / 111.0

    cos_latitude = abs(
        math.cos(math.radians(latitude))
    )

    if cos_latitude < 1e-6:
        raise ValueError(
            "This radius-based bounding box is unsuitable near the poles."
        )

    lon_offset = radius_km / (111.0 * cos_latitude)

    min_lon = longitude - lon_offset
    max_lon = longitude + lon_offset
    min_lat = max(-90.0, latitude - lat_offset)
    max_lat = min(90.0, latitude + lat_offset)

    aoi_shape = box(
        min_lon, min_lat, max_lon, max_lat
    )

    geometry = aoi_shape.__geo_interface__

    print("\nCoordinate-based AOI bounding box:")
    print(f"Longitude: {min_lon:.6f} to {max_lon:.6f}")
    print(f"Latitude:  {min_lat:.6f} to {max_lat:.6f}")

else:
    raise ValueError("Choose AOI input method 1 or 2.")

# ============================================================
# 2. MONTHLY DATE AND PROCESSING INPUTS
# ============================================================

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
print(f"AOI mode: {aoi_mode}")

if admin1_name is not None:
    print(f"Admin 1: {admin1_name}")

if admin2_name is not None:
    print(f"Admin 2: {admin2_name}")

if admin3_name is not None:
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
# ============================================================
# 7B. CONVERT OPTICAL BANDS TO SURFACE REFLECTANCE
# ============================================================

REFLECTANCE_SCALE = 0.0001
SOURCE_NODATA = 0

offset_flags = {
    item.properties.get("earthsearch:boa_offset_applied")
    for item in items
}

if len(offset_flags) != 1 or None in offset_flags:
    raise RuntimeError(
        "Scenes have missing or inconsistent BOA offset flags. "
        "Do not composite them until the reflectance convention "
        "has been verified."
    )

boa_offset_already_applied = offset_flags.pop()

print("\nREFLECTANCE SCALING CHECK")
print("BOA offset already applied:", boa_offset_already_applied)

for band_name in ["red", "green", "blue", "nir"]:
    raw_band = data[band_name]

    # Exclude source NoData and zeros introduced outside the AOI.
    valid_pixels = (raw_band != SOURCE_NODATA) & raw_band.notnull()

    if boa_offset_already_applied:
        reflectance = (
            raw_band.astype("float32") * REFLECTANCE_SCALE
        )
        offset_used = 0.0
    else:
        reflectance = (
            raw_band.astype("float32") * REFLECTANCE_SCALE - 0.1
        )
        offset_used = -0.1

    data[band_name] = reflectance.where(valid_pixels)

    data[band_name].attrs.update({
        "units": "surface reflectance",
        "scale_applied": REFLECTANCE_SCALE,
        "offset_applied_in_pipeline": offset_used,
        "earthsearch_boa_offset_already_applied":
            boa_offset_already_applied,
        "source_nodata": SOURCE_NODATA,
    })

print("Reflectance conversion completed.")
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

# Keep NIR as well as RGB because NDVI requires red and NIR.
# NaN values from masking are ignored by mean/median.
processing_bands = ["red", "green", "blue", "nir"]

data = masked_data[processing_bands]

data = create_temporal_composite(
    data,
    method=composite_method,
)

print("Masked temporal composite created successfully.")
print(data)
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

if process == "RGB":
    output_file = OUTPUT_DIR / "rgb.tif"
elif process == "NDVI":
    output_file = OUTPUT_DIR / "ndvi.tif"
else:
    raise ValueError(f"Unsupported processing option: {process}")

print(f"\nExporting {process} product to: {output_file}")

export_cog(result, output_file)

print("COG export completed.")

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

preview_file = OUTPUT_DIR / (
    "rgb_preview.png"
    if process == "RGB"
    else "ndvi_preview.png"
)

if process == "RGB":

    print("Creating reduced-resolution RGB preview...")

    save_rgb_preview(
        result,
        preview_file,
        max_preview_dimension=1600,
    )

    print("Preview saved:", preview_file)

elif process == "NDVI":

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
