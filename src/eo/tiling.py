"""Spatial tiling helpers for large-area Sentinel-2 processing."""

from collections import Counter
from math import ceil, floor

from pyproj import CRS, Transformer
from shapely.geometry import box
from shapely.ops import clip_by_rect
from shapely.ops import transform as transform_geometry
from shapely.prepared import prep

try:  # Shapely >= 2.0
    from shapely import segmentize
except ImportError:  # pragma: no cover
    segmentize = None


# Tile edges must fall on both the 10 m and 20 m Sentinel-2 pixel grids.
PIXEL_GRID_M = 20


def _item_crs(item):
    """Return the projected CRS advertised by one STAC item, or None."""

    candidates = [
        item.properties.get("proj:epsg"),
        item.properties.get("proj:code"),
    ]

    for asset in item.assets.values():
        candidates.extend([
            asset.extra_fields.get("proj:epsg"),
            asset.extra_fields.get("proj:code"),
        ])

    for candidate in candidates:
        if candidate is None:
            continue

        try:
            return CRS.from_user_input(candidate).to_string()
        except (ValueError, TypeError):
            continue

    return None


def choose_target_crs(items, aoi_shape):
    """
    Choose one projected CRS for every tile.

    Uses the CRS shared by most input scenes, so one scene at the edge of a
    large AOI cannot decide the projection. If no projection metadata is
    present, estimate a UTM CRS from a point inside the AOI.

    aoi_shape must be a Shapely geometry in EPSG:4326.
    """

    counts = Counter()

    for item in items:
        crs_string = _item_crs(item)
        if crs_string:
            counts[crs_string] += 1

    if counts:
        # Ties resolve to the CRS seen first.
        return counts.most_common(1)[0][0]

    point = aoi_shape.representative_point()
    longitude = point.x
    latitude = point.y

    if not -80 <= latitude <= 84:
        raise ValueError(
            "Automatic UTM selection supports latitudes "
            "between 80 degrees south and 84 degrees north. "
            "Specify an appropriate projected target CRS."
        )

    zone = max(1, min(60, int((longitude + 180) // 6) + 1))

    epsg_code = (32600 if latitude >= 0 else 32700) + zone
    return f"EPSG:{epsg_code}"


def build_aoi_tiles(
    aoi_shape,
    tile_size_m=5000,
    target_crs="EPSG:32636",
    min_area_m2=400.0,
    max_segment_degrees=0.02,
):
    """
    Split a WGS84 AOI into aligned square tiles.

    Each tile record contains:
      tile_id: unique tile label
      bounds: projected xmin, ymin, xmax, ymax
      aoi_geometry_wgs84: exact AOI intersection for clipping

    min_area_m2: tiles whose AOI overlap is smaller than this are skipped
        (the default is one 20 m pixel).
    max_segment_degrees: long AOI edges are subdivided before projection so
        straight lat/lon edges stay accurate in the target CRS.
    """

    if aoi_shape is None or aoi_shape.is_empty:
        raise ValueError("AOI geometry is empty.")

    if not aoi_shape.is_valid:
        raise ValueError("AOI geometry is invalid.")

    if tile_size_m != int(tile_size_m) or tile_size_m <= 0:
        raise ValueError("Tile size must be a positive whole number of metres.")

    tile_size_m = int(tile_size_m)

    if tile_size_m % PIXEL_GRID_M != 0:
        raise ValueError(
            f"Tile size must be a multiple of {PIXEL_GRID_M} m so tile edges "
            "stay on the Sentinel-2 pixel grid."
        )

    target_crs = CRS.from_user_input(target_crs).to_string()

    to_projected = Transformer.from_crs(
        "EPSG:4326", target_crs, always_xy=True
    ).transform

    to_wgs84 = Transformer.from_crs(
        target_crs, "EPSG:4326", always_xy=True
    ).transform

    source_shape = aoi_shape

    if segmentize is not None and max_segment_degrees:
        source_shape = segmentize(aoi_shape, max_segment_degrees)

    aoi_projected = transform_geometry(to_projected, source_shape)

    if not aoi_projected.is_valid:
        aoi_projected = aoi_projected.buffer(0)

    prepared = prep(aoi_projected)

    min_x, min_y, max_x, max_y = aoi_projected.bounds

    # Align tile edges to multiples of the tile size.
    x_start = floor(min_x / tile_size_m) * tile_size_m
    y_start = floor(min_y / tile_size_m) * tile_size_m
    x_stop = ceil(max_x / tile_size_m) * tile_size_m
    y_stop = ceil(max_y / tile_size_m) * tile_size_m

    tiles = []

    for row, y_min in enumerate(range(y_start, y_stop, tile_size_m)):
        for col, x_min in enumerate(range(x_start, x_stop, tile_size_m)):
            x_max = x_min + tile_size_m
            y_max = y_min + tile_size_m

            tile_box = box(x_min, y_min, x_max, y_max)

            # Cheap rejection and cheap acceptance before any overlay.
            if not prepared.intersects(tile_box):
                continue

            if prepared.contains(tile_box):
                clipped_geometry = tile_box
            else:
                clipped_geometry = clip_by_rect(
                    aoi_projected, x_min, y_min, x_max, y_max
                )

                if not clipped_geometry.is_valid:
                    clipped_geometry = clipped_geometry.buffer(0)

            if (
                clipped_geometry.is_empty
                or clipped_geometry.area < min_area_m2
            ):
                continue

            tiles.append({
                "tile_id": f"r{row:04d}_c{col:04d}",
                "bounds": (
                    float(x_min),
                    float(y_min),
                    float(x_max),
                    float(y_max),
                ),
                "aoi_geometry_wgs84": transform_geometry(
                    to_wgs84, clipped_geometry
                ),
            })

    return tiles

def build_single_aoi_window(
    aoi_shape,
    target_crs="EPSG:32636",
    pixel_size_m=PIXEL_GRID_M,
    max_window_size_m=10000,
    max_segment_degrees=0.02,
):
    """Build one pixel-aligned window for a small WGS84 AOI.

    Returns one tile record, or an empty list when the AOI is too large
    for the configured maximum window dimensions.
    """
    if aoi_shape is None or aoi_shape.is_empty:
        raise ValueError("AOI geometry is empty.")

    if not aoi_shape.is_valid:
        raise ValueError("AOI geometry is invalid.")

    if pixel_size_m <= 0 or pixel_size_m % PIXEL_GRID_M != 0:
        raise ValueError(
            f"Pixel size must be a positive multiple of {PIXEL_GRID_M} m."
        )

    if max_window_size_m <= 0:
        raise ValueError("Maximum window size must be positive.")

    target_crs = CRS.from_user_input(target_crs).to_string()

    to_projected = Transformer.from_crs(
        "EPSG:4326", target_crs, always_xy=True
    ).transform

    source_shape = aoi_shape
    if segmentize is not None and max_segment_degrees:
        source_shape = segmentize(aoi_shape, max_segment_degrees)

    projected = transform_geometry(to_projected, source_shape)
    if not projected.is_valid:
        projected = projected.buffer(0)

    min_x, min_y, max_x, max_y = projected.bounds

    x_min = floor(min_x / pixel_size_m) * pixel_size_m
    y_min = floor(min_y / pixel_size_m) * pixel_size_m
    x_max = ceil(max_x / pixel_size_m) * pixel_size_m
    y_max = ceil(max_y / pixel_size_m) * pixel_size_m

    width = x_max - x_min
    height = y_max - y_min

    if width <= 0 or height <= 0:
        raise ValueError("AOI produces an empty processing window.")

    if width > max_window_size_m or height > max_window_size_m:
        return []

    return [{
        "tile_id": "window",
        "bounds": (
            float(x_min),
            float(y_min),
            float(x_max),
            float(y_max),
        ),
        # Keep the exact input geometry for final raster masking.
        "aoi_geometry_wgs84": aoi_shape,
    }]
