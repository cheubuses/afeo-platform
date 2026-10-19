# ============================================================
# SENTINEL-2 CLOUD-NATIVE EO PROCESSING
# Coordinate / Admin area -> STAC -> Tiled temporal composite
# -> RGB / indices -> COG + preview + metadata
# ============================================================

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless-safe backend

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling
from shapely.geometry import box
from odc.stac import configure_s3_access

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from eo.stac import connect_to_catalog
from eo.aoi import (
    list_admin1,
    list_admin2,
    list_admin3,
    get_admin_geometry,
)
from eo.sentinel2 import search_sentinel2, select_best_scene
from eo.tiled_pipeline import process_tiled_aoi


PRODUCT_OPTIONS = {
    "1": "RGB",
    "2": "FALSE_COLOR",
    "3": "NDVI",
    "4": "NDWI",
    "5": "NDMI",
    "6": "NBR",
    "7": "NBR2",
}

PRODUCT_LABELS = {
    "RGB": "True-colour composite",
    "FALSE_COLOR": "False-colour infrared composite",
    "NDVI": "Vegetation index",
    "NDWI": "Open-water index",
    "NDMI": "Vegetation moisture index",
    "NBR": "Normalized Burn Ratio",
    "NBR2": "Normalized Burn Ratio 2",
}

ALIASES = {
    "FALSE_COLOUR": "FALSE_COLOR",
    "FALSE_COLOR_IR": "FALSE_COLOR",
    "NWI": "NDWI",
    "WATER_INDEX": "NDWI",
}

PREVIEW_NAMES = {
    "RGB": "rgb_preview.png",
    "FALSE_COLOR": "false_color_preview.png",
    "NDVI": "ndvi_preview.png",
    "NDWI": "ndwi_preview.png",
    "NDMI": "ndmi_preview.png",
    "NBR": "nbr_preview.png",
    "NBR2": "nbr2_preview.png",
}


# ============================================================
# INPUT HELPERS
# ============================================================

def choose_from_list(options, label):
    print(f"\nAvailable {label}:")
    for index, name in enumerate(options, start=1):
        print(f"{index}. {name}")

    selection = int(input(f"\nSelect {label} by number: ").strip())

    if not 1 <= selection <= len(options):
        raise ValueError(f"Invalid {label} selection.")

    return options[selection - 1]


def collect_aoi():
    """Return (aoi_shape, input_parameters)."""

    print("\nSelect AOI input method:")
    print("1. Administrative area from FieldMaps GeoParquet")
    print("2. Latitude, longitude and radius")

    aoi_mode = input("Enter choice (1 or 2): ").strip()

    params = {
        "latitude": None,
        "longitude": None,
        "radius_km": None,
        "country_code": None,
        "admin_level": None,
        "admin1_name": None,
        "admin2_name": None,
        "admin3_name": None,
    }

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

        admin1_name = admin2_name = admin3_name = None

        options = list_admin1(country_code)
        if not options:
            raise RuntimeError(f"No Admin 1 areas found for {country_code}.")
        admin1_name = choose_from_list(options, "Admin 1 area")

        if admin_level >= 2:
            options = list_admin2(admin1_name, country_code)
            if not options:
                raise RuntimeError(
                    f"No Admin 2 areas found within {admin1_name}."
                )
            admin2_name = choose_from_list(options, "Admin 2 area")

        if admin_level == 3:
            options = list_admin3(admin1_name, admin2_name, country_code)
            if not options:
                raise RuntimeError(
                    f"No Admin 3 areas found within {admin2_name}."
                )
            admin3_name = choose_from_list(options, "Admin 3 area")

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

        params.update(
            country_code=country_code,
            admin_level=admin_level,
            admin1_name=admin1_name,
            admin2_name=admin2_name,
            admin3_name=admin3_name,
        )

        print("\nSelected administrative AOI:")
        print(f"Country: {country_code}  |  Level: {admin_level}")
        print(f"Admin 1: {admin1_name}")
        if admin2_name:
            print(f"Admin 2: {admin2_name}")
        if admin3_name:
            print(f"Admin 3: {admin3_name}")
        print(f"Geometry: {aoi_shape.geom_type}, bounds {aoi_shape.bounds}")

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

        params.update(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
        )

        print("\nCoordinate-based AOI bounding box:")
        print(f"Longitude: {min_lon:.6f} to {max_lon:.6f}")
        print(f"Latitude:  {min_lat:.6f} to {max_lat:.6f}")

    else:
        raise ValueError("Choose AOI input method 1 or 2.")

    return aoi_shape, params


def collect_dates():
    year = int(input("Enter year (e.g. 2026): "))
    month = int(input("Enter month (1-12): "))

    if not 1 <= month <= 12:
        raise ValueError("Month must be between 1 and 12.")

    start = date(year, month, 1)
    next_month = (
        date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    )
    end = next_month - timedelta(days=1)

    return start.isoformat(), end.isoformat()


def collect_product():
    print("\nAvailable processing products:")
    for number, name in PRODUCT_OPTIONS.items():
        print(f"{number}. {name:<12} - {PRODUCT_LABELS[name]}")

    choice = (
        input("\nSelect product (enter number or name): ")
        .strip()
        .upper()
        .replace(" ", "_")
        .replace("-", "_")
    )

    product = PRODUCT_OPTIONS.get(choice, choice)
    product = ALIASES.get(product, product)

    if product == "QUALITY_MASK":
        raise ValueError(
            "QUALITY_MASK is not implemented in the tiled pipeline yet."
        )

    if product not in PREVIEW_NAMES:
        raise ValueError(
            f"Unsupported product: {product}. "
            f"Choose one of: {', '.join(sorted(PREVIEW_NAMES))}."
        )

    return product


# ============================================================
# SCENE SELECTION
# ============================================================

def baseline_version(item):
    value = item.properties.get("s2:processing_baseline") or "0"
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return (0,)


def deduplicate_items(items):
    """Keep the highest processing baseline for each acquisition."""

    by_acquisition = {}

    for item in items:
        parts = item.id.split("_")
        # S2B_43QHC_20200226_1_L2A -> S2B_43QHC_20200226
        key = "_".join(parts[:3]) if len(parts) >= 3 else item.id

        previous = by_acquisition.get(key)
        if previous is None or baseline_version(item) > baseline_version(
            previous
        ):
            by_acquisition[key] = item

    return sorted(
        by_acquisition.values(),
        key=lambda item: item.properties.get("datetime", ""),
    )


# ============================================================
# PREVIEW (reads COG overviews, cheap at any AOI size)
# ============================================================

def save_preview(cog_path, product, preview_file, max_dimension=1600):
    with rasterio.open(cog_path) as src:
        factor = max(1.0, max(src.height, src.width) / max_dimension)
        out_h = max(1, int(src.height / factor))
        out_w = max(1, int(src.width / factor))

        array = src.read(
            out_shape=(src.count, out_h, out_w),
            masked=True,
            resampling=Resampling.average,
        )

    if product in {"RGB", "FALSE_COLOR"} and array.shape[0] >= 3:
        bands = np.ma.filled(array[:3].astype("float32"), np.nan)
        valid = np.isfinite(bands).all(axis=0)

        rgba = np.zeros((out_h, out_w, 4), dtype="float32")

        if valid.any():
            for i in range(3):
                lo, hi = np.nanpercentile(bands[i][valid], (2, 98))
                if hi <= lo:
                    hi = lo + 1e-6
                rgba[..., i] = np.clip((bands[i] - lo) / (hi - lo), 0, 1)

        rgba[..., 3] = valid.astype("float32")
        plt.imsave(preview_file, rgba)

    else:
        values = np.ma.filled(array[0].astype("float32"), np.nan)
        values = np.where(
            np.isfinite(values) & (values >= -1) & (values <= 1),
            values,
            np.nan,
        )

        cmap = plt.get_cmap("RdYlGn").copy()
        cmap.set_bad((0, 0, 0, 0))

        fig, ax = plt.subplots(figsize=(8, 7))
        image = ax.imshow(
            np.ma.masked_invalid(values), cmap=cmap, vmin=-1, vmax=1
        )
        fig.colorbar(image, ax=ax, label=product)
        ax.set_title(f"Sentinel-2 {product} Preview")
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(
            preview_file, dpi=150, bbox_inches="tight", transparent=True
        )
        plt.close(fig)

    print("Preview saved:", preview_file)


# ============================================================
# MAIN
# ============================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    configure_s3_access(aws_unsigned=True)

    # ---- Inputs --------------------------------------------------
    aoi_shape, input_parameters = collect_aoi()
    geometry = aoi_shape.__geo_interface__

    start_date, end_date = collect_dates()

    composite_method = (
        input("Choose composite method (median/mean): ").strip().lower()
    )
    if composite_method not in {"median", "mean"}:
        raise ValueError("Choose either median or mean.")

    cloud_cover = float(
        input("Enter maximum scene cloud cover (0-100%): ")
    )
    if not 0 <= cloud_cover <= 100:
        raise ValueError("Cloud cover must be between 0 and 100.")

    process = collect_product()

    tile_size_m = int(
        input("Tile size in metres (default 5000): ").strip() or 5000
    )
    max_workers = int(
        input("Parallel worker processes (default 1): ").strip() or 1
    )

    scl_retained_classes = None

    if process in {"NBR", "NBR2"}:
        keep7 = input(
            "Keep SCL class 7 (unclassified; often burned areas)? [Y/n]: "
        ).strip().lower()
        if keep7 in {"", "y", "yes"}:
            scl_retained_classes = (4, 5, 6, 7)

    print("\nUser inputs:")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Composite method: {composite_method}")
    print(f"Maximum scene cloud cover: {cloud_cover}%")
    print(f"Requested product: {process}")
    print(f"Tile size: {tile_size_m} m | Workers: {max_workers}")
    print(f"SCL classes retained: {scl_retained_classes or (4, 5, 6)}")

    # ---- STAC search --------------------------------------------
    print("\nConnecting to Earth Search STAC...")
    catalog = connect_to_catalog()
    print("STAC connection successful.")

    print("\nSearching for Sentinel-2 imagery...")
    items = search_sentinel2(
        catalog, geometry, start_date, end_date, cloud_cover
    )

    if not items:
        raise RuntimeError(
            "No Sentinel-2 scenes matched the AOI, date range, "
            "and cloud-cover threshold. Try a wider date range "
            "or a higher cloud-cover threshold."
        )

    print(f"Search returned {len(items)} item(s).")

    items = deduplicate_items(items)

    print(
        f"Retained {len(items)} scene(s) after selecting the highest "
        "processing baseline per acquisition."
    )

    first = items[0]
    print("STAC collection:", first.collection_id)
    print("Available assets:", list(first.assets.keys()))

    for item in items:
        cc = item.properties.get("eo:cloud_cover")
        cloud_text = "unknown" if cc is None else f"{cc:.2f}%"
        print(f"{item.id} | Cloud cover: {cloud_text}")

    best_item = select_best_scene(items)
    if best_item is None:
        raise RuntimeError("Could not select a suitable Sentinel-2 scene.")

    print(
        f"\nReference scene: {best_item.id} "
        f"(cloud cover {best_item.properties.get('eo:cloud_cover')})"
    )

    # ---- Tiled processing ---------------------------------------
    print("\nStarting tiled Sentinel-2 processing...")

    result = process_tiled_aoi(
        items=items,
        aoi_shape=aoi_shape,
        process=process,
        composite_method=composite_method,
        start_date=start_date,
        end_date=end_date,
        cloud_cover_threshold=cloud_cover,
        reference_item=best_item,
        output_dir=OUTPUT_DIR,
        project_root=PROJECT_ROOT,
        input_parameters=input_parameters,
        tile_size_m=tile_size_m,
        max_workers=max_workers,
        scl_retained_classes=scl_retained_classes,
    )

    # ---- Preview from the final COG -----------------------------
    preview_file = OUTPUT_DIR / PREVIEW_NAMES[process]
    save_preview(result["output_file"], process, preview_file)

    # ---- Summary -------------------------------------------------
    print("\n==========================================")
    print("       PROCESSING COMPLETED")
    print("==========================================")
    print(f"Product:     {process}")
    print(f"Composite:   {composite_method}")
    print(f"Tile count:  {result['tile_count']}")
    print(f"COG:         {result['output_file']}")
    print(f"Valid count: {result['valid_count_file']}")
    print(f"Preview:     {preview_file}")
    print(f"Metadata:    {result['metadata_file']}")
    print(f"Manifest:    {result['manifest_file']}")
    print(f"Elapsed:     {result['elapsed_seconds']:.2f} seconds")
    print("==========================================")


# The guard is required: parallel workers are spawned and re-import this
# module, which would otherwise re-run every input() prompt.
if __name__ == "__main__":
    main()
