
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
# 2A. MONTHLY DATE AND PROCESSING INPUTS
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

# ============================================================
# DIAGNOSTIC: INSPECT SOURCE ASSET METADATA
# ============================================================

if not items:
    raise RuntimeError(
        "No Sentinel-2 scenes matched the AOI, date range, "
        "and cloud-cover threshold. Try a wider date range "
        "or a higher cloud-cover threshold."
    )

# Only inspect the first item after confirming items exist.
item = items[0]
2

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

# TEMPORARY: inspect values returned by stac_load BEFORE scaling

for band_name in ["red", "nir"]:
    band = data[band_name]
    sample = band.isel(time=0).compute().values
    finite = sample[np.isfinite(sample)]

    print(f"\n--- {band_name.upper()} BEFORE SECTION 7A ---")
    print("Dtype:", band.dtype)
    print("Attributes:", band.attrs)

    if finite.size:
        print("Minimum:", float(finite.min()))
        print("Median:", float(np.median(finite)))
        print("Maximum:", float(finite.max()))
        print("Zero values:", int(np.count_nonzero(finite == 0)))
        print("Negative values:", int(np.count_nonzero(finite < 0)))

# ============================================================
# 7A. CONVERT OPTICAL BANDS TO SURFACE REFLECTANCE
# ============================================================

# Earth Search asset metadata reports scale=0.0001
# and offset=-0.1 for the inspected red and NIR assets.
# Apply this transformation once to the raw DN values.
# Keep SCL unchanged because it contains class codes.

REFLECTANCE_SCALE = 0.0001
REFLECTANCE_OFFSET = -0.1

for band_name in ["red", "green", "blue", "nir"]:
    raw_band = data[band_name]

    # Zero is the declared source NoData value.
    valid_pixels = raw_band != 0

    data[band_name] = (
        raw_band.astype("float32")
        * REFLECTANCE_SCALE
        + REFLECTANCE_OFFSET
    ).where(valid_pixels)

    data[band_name].attrs.update({
        "units": "surface reflectance",
        "scale_applied": REFLECTANCE_SCALE,
        "offset_applied": REFLECTANCE_OFFSET,
        "source_nodata": 0,
    })

print("\nOptical bands converted to surface reflectance.")
print("Applied formula: reflectance = DN * 0.0001 - 0.1")

# Diagnostic: inspect loaded reflectance bands before NDVI calculation.
for band_name in ["red", "nir"]:
    band = data[band_name]

    print(f"\n--- {band_name.upper()} band ---")
    print("Data type:", band.dtype)
    print("Attributes:", band.attrs)

    sample = band.isel(time=0).compute().values
    valid = sample[np.isfinite(sample)]

    if valid.size:
        print("Minimum:", float(valid.min()))
        print("Maximum:", float(valid.max()))
        print("Mean:", float(valid.mean()))




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


#
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

# The SCL layer is no longer needed in the spectral composite.
# NaN values from masking are ignored by mean/median.
data = masked_data[required_bands]

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
    data,
    after_preview_file,
    stretch_limits=rgb_stretch_limits,
)

print("After-masking preview:", after_preview_file)


# ============================================================
# 9. PROCESS THE REQUESTED PRODUCT
# ============================================================

print(f"\nRunning {process} processing...")


# TEMPORARY NDVI INPUT DIAGNOSTICS
if process == "NDVI":
    import numpy as np

    red_values = data["red"].compute().values.astype("float32")
    nir_values = data["nir"].compute().values.astype("float32")

    valid = np.isfinite(red_values) & np.isfinite(nir_values)
    red_valid = red_values[valid]
    nir_valid = nir_values[valid]
    denominator = red_valid + nir_valid

    print("\n--- NDVI INPUT DIAGNOSTICS ---")
    print("Finite red pixels:", red_valid.size)
    print("Red min/median/max:",
          np.min(red_valid), np.median(red_valid), np.max(red_valid))
    print("NIR min/median/max:",
          np.min(nir_valid), np.median(nir_valid), np.max(nir_valid))
    print("Negative red pixels:", np.sum(red_valid < 0))
    print("Negative NIR pixels:", np.sum(nir_valid < 0))
    print("Zero denominators:", np.sum(denominator == 0))
    print("Near-zero denominators (abs < 1e-6):",
          np.sum(np.abs(denominator) < 1e-6))
    print("Smallest absolute denominator:",
          np.min(np.abs(denominator)))
    print("--- END DIAGNOSTICS ---\n")



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

   
    # RGB processing returns a DataArray with dimensions:
    # band, y, x
    rgb_data = result.compute()

    print("RGB dimensions:", rgb_data.dims)
    print("Band labels:", rgb_data.coords["band"].values)

    # Extract the bands by position.
    # This assumes create_rgb() stacks red, green, blue in that order.
    rgb_data = rgb_data.transpose("band", "y", "x")

    if rgb_data.sizes["band"] != 3:
        raise ValueError(
            f"Expected 3 RGB bands, got {rgb_data.sizes['band']}."
        )

    
    # Extract bands using their confirmed labels
    band_labels = [
        str(label)
        for label in rgb_data.coords["band"].values
    ]

    expected_bands = ["red", "green", "blue"]

    if band_labels != expected_bands:
        raise ValueError(
            f"Unexpected RGB band order: {band_labels}. "
            f"Expected {expected_bands}."
        )

    bands = [
        np.asarray(
            rgb_data.sel(band=band).values,
            dtype=np.float32,
        ).squeeze()
        for band in expected_bands
    ]

    if any(band.ndim != 2 for band in bands):
        raise ValueError("Each RGB band must be a 2D array.")

    if not (bands[0].shape == bands[1].shape == bands[2].shape):
        raise ValueError("RGB bands have different spatial shapes.")

    # Shape: (height, width, 3)
    rgb_array = np.stack(bands, axis=-1)

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
