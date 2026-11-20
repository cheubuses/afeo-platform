"""Geometric comparison of AIRSAT raster and vendor footprints."""

from __future__ import annotations

from math import isfinite
from typing import Sequence

from pyproj import Geod, Transformer
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
from shapely import transform


_GEOD = Geod(ellps="WGS84")


def _polygonal(geometry):
    """Keep polygon components after geometry repair or overlay."""
    if geometry.is_empty:
        return GeometryCollection()

    if geometry.geom_type == "Polygon":
        return geometry

    if geometry.geom_type == "MultiPolygon":
        return geometry

    if geometry.geom_type == "GeometryCollection":
        parts = [
            part
            for item in geometry.geoms
            if (part := _polygonal(item)).geom_type
            in ("Polygon", "MultiPolygon")
            and not part.is_empty
        ]

        polygons = []
        for part in parts:
            if part.geom_type == "Polygon":
                polygons.append(part)
            else:
                polygons.extend(part.geoms)

        if not polygons:
            return GeometryCollection()

        return MultiPolygon(polygons)

    return GeometryCollection()


def _valid_polygon(coordinates: Sequence[Sequence[float]], label: str):
    """Create and validate a WGS84 polygon from longitude/latitude pairs."""
    if len(coordinates) < 4:
        raise ValueError(f"{label} must contain at least four coordinates.")

    points = []
    for coordinate in coordinates:
        if len(coordinate) < 2:
            raise ValueError(f"{label} contains an invalid coordinate.")

        longitude = float(coordinate[0])
        latitude = float(coordinate[1])

        if not (
            isfinite(longitude)
            and isfinite(latitude)
            and -180 <= longitude <= 180
            and -90 <= latitude <= 90
        ):
            raise ValueError(f"{label} contains out-of-range coordinates.")

        points.append((longitude, latitude))

    polygon = Polygon(points)

    if not polygon.is_valid:
        polygon = _polygonal(polygon.buffer(0))

    if polygon.is_empty or polygon.area == 0:
        raise ValueError(f"{label} does not form a valid polygon.")

    return polygon


def _geodesic_area(geometry) -> float:
    """Return the absolute WGS84 geodesic area in square metres."""
    if geometry.is_empty:
        return 0.0

    area, _ = _GEOD.geometry_area_perimeter(geometry)
    return abs(float(area))


def compare_footprints(
    raster_bounds: Sequence[float],
    vendor_coordinates: Sequence[Sequence[float]],
) -> dict:
    """
    Compare a raster's rectangular bounds with the vendor scene footprint.

    Parameters
    ----------
    raster_bounds:
        Bounds in EPSG:4326 ordered as (left, bottom, right, top).
    vendor_coordinates:
        Vendor footprint coordinates ordered as longitude, latitude.
        The ring may be open or closed.

    Returns
    -------
    dict
        Geodesic area, intersection-over-union, symmetric difference,
        and local projected boundary-distance metrics.
    """
    if len(raster_bounds) != 4:
        raise ValueError(
            "Raster bounds must be (left, bottom, right, top)."
        )

    left, bottom, right, top = map(float, raster_bounds)

    if not all(map(isfinite, (left, bottom, right, top))):
        raise ValueError("Raster bounds contain non-finite values.")

    if not (
        -180 <= left < right <= 180
        and -90 <= bottom < top <= 90
    ):
        raise ValueError(
            "Raster bounds are invalid for an EPSG:4326 raster."
        )

    raster_polygon = box(left, bottom, right, top)
    vendor_polygon = _valid_polygon(
        vendor_coordinates,
        "Vendor footprint",
    )

    intersection = _polygonal(
        raster_polygon.intersection(vendor_polygon)
    )
    union = _polygonal(
        raster_polygon.union(vendor_polygon)
    )
    symmetric_difference = _polygonal(
        raster_polygon.symmetric_difference(vendor_polygon)
    )

    raster_area = _geodesic_area(raster_polygon)
    vendor_area = _geodesic_area(vendor_polygon)
    intersection_area = _geodesic_area(intersection)
    union_area = _geodesic_area(union)
    difference_area = _geodesic_area(symmetric_difference)

    iou = (
        intersection_area / union_area
        if union_area > 0
        else 0.0
    )

    # Project both boundaries into a local azimuthal-equidistant CRS.
    # This gives a meaningful distance in metres for this local scene.
    centre = vendor_polygon.centroid
    local_crs = (
        f"+proj=aeqd +lat_0={centre.y} +lon_0={centre.x} "
        "+datum=WGS84 +units=m +no_defs"
    )
    transformer = Transformer.from_crs(
        "EPSG:4326",
        local_crs,
        always_xy=True,
    )

    project = transformer.transform
    raster_local = transform(raster_polygon, project, interleaved=False)
    vendor_local = transform(vendor_polygon, project, interleaved=False)

    boundary_distance_m = float(
        raster_local.boundary.hausdorff_distance(
            vendor_local.boundary
        )
    )

    return {
        "comparison_method": (
            "WGS84 geodesic area; local azimuthal-equidistant "
            "boundary distance"
        ),
        "coordinate_reference_system": "EPSG:4326",
        "raster_area_m2": round(raster_area, 3),
        "vendor_footprint_area_m2": round(vendor_area, 3),
        "intersection_area_m2": round(intersection_area, 3),
        "union_area_m2": round(union_area, 3),
        "symmetric_difference_area_m2": round(
            difference_area, 3
        ),
        "intersection_over_union": round(iou, 8),
        "intersection_over_union_percent": round(iou * 100, 4),
        "symmetric_difference_percent_of_union": round(
            difference_area / union_area * 100
            if union_area > 0
            else 0.0,
            4,
        ),
        "boundary_hausdorff_distance_m": round(
            boundary_distance_m, 3
        ),
        "raster_bounds": {
            "left": left,
            "bottom": bottom,
            "right": right,
            "top": top,
        },
        "vendor_footprint_bounds": {
            "left": round(vendor_polygon.bounds[0], 10),
            "bottom": round(vendor_polygon.bounds[1], 10),
            "right": round(vendor_polygon.bounds[2], 10),
            "top": round(vendor_polygon.bounds[3], 10),
        },
    }
