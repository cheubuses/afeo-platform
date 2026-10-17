"""Spatial tiling helpers for large-area Sentinel-2 processing."""

from math import ceil, floor

from pyproj import CRS, Transformer
from shapely.geometry import box
from shapely.ops import transform as transform_geometry


def choose_target_crs(items, aoi_shape):
    """
    Choose one projected CRS for every tile.

    Prefer the CRS advertised by the input STAC assets.
    If projection metadata is absent, estimate a UTM CRS
    from a representative point inside the AOI.

    aoi_shape must be a Shapely geometry in EPSG:4326.
    """
    for item in items:
        candidates = [
            item.properties.get("proj:epsg"),
            item.properties.get("proj:code"),
        ]

        # Asset-level projection metadata can also be present.
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

    point = aoi_shape.representative_point()
    longitude = point.x
    latitude = point.y

    if not -80 <= latitude <= 84:
        raise ValueError(
            "Automatic UTM selection supports latitudes "
            "between 80 degrees south and 84 degrees north. "
            "Specify an appropriate projected target CRS."
        )

    zone = max(
        1,
        min(60, int((longitude + 180) // 6) + 1),
    )

    epsg_code = (32600 if latitude >= 0 else 32700) + zone
    return f"EPSG:{epsg_code}"


def build_aoi_tiles(
    aoi_shape,
    tile_size_m=5000,
    target_crs="EPSG:32636",
):
    """
    Split a WGS84 AOI into aligned square tiles.

    Each tile record contains:
      tile_id: unique tile label
      bounds: projected xmin, ymin, xmax, ymax
      aoi_geometry_wgs84: exact AOI intersection for clipping
    """
    if aoi_shape is None or aoi_shape.is_empty:
        raise ValueError("AOI geometry is empty.")

    if not aoi_shape.is_valid:
        raise ValueError("AOI geometry is invalid.")

    if tile_size_m <= 0:
        raise ValueError("Tile size must be greater than zero.")

    target_crs = CRS.from_user_input(target_crs).to_string()

    to_projected = Transformer.from_crs(
        "EPSG:4326",
        target_crs,
        always_xy=True,
    ).transform

    to_wgs84 = Transformer.from_crs(
        target_crs,
        "EPSG:4326",
        always_xy=True,
    ).transform

    aoi_projected = transform_geometry(to_projected, aoi_shape)

    min_x, min_y, max_x, max_y = aoi_projected.bounds

    # Align tile edges to multiples of the tile size.
    x_start = floor(min_x / tile_size_m) * tile_size_m
    y_start = floor(min_y / tile_size_m) * tile_size_m
    x_stop = ceil(max_x / tile_size_m) * tile_size_m
    y_stop = ceil(max_y / tile_size_m) * tile_size_m

    tiles = []

    for row, y_min in enumerate(
        range(y_start, y_stop, tile_size_m)
    ):
        for col, x_min in enumerate(
            range(x_start, x_stop, tile_size_m)
        ):
            x_max = x_min + tile_size_m
            y_max = y_min + tile_size_m

            tile_box = box(x_min, y_min, x_max, y_max)
            clipped_geometry = aoi_projected.intersection(tile_box)

            if clipped_geometry.is_empty or clipped_geometry.area == 0:
                continue

            tile_aoi_wgs84 = transform_geometry(
                to_wgs84,
                clipped_geometry,
            )

            tiles.append({
                "tile_id": f"r{row:04d}_c{col:04d}",
                "bounds": (
                    float(x_min),
                    float(y_min),
                    float(x_max),
                    float(y_max),
                ),
                "aoi_geometry_wgs84": tile_aoi_wgs84,
            })

    return tiles
