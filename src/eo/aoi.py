"""
Administrative Area of Interest (AOI) selection.

Reads FieldMaps administrative boundaries directly from remote GeoParquet
using DuckDB Spatial.

Admin 1: County, e.g. Baringo
Admin 2: Sub-county, e.g. Baringo Central
Admin 3: Ward, e.g. Ewalel Chapchap

Geometry coordinates are longitude, latitude (OGC:CRS84).
"""

import time
from functools import lru_cache

import duckdb
from shapely import wkb
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid


BOUNDARIES_URL = (
    "https://data.fieldmaps.io/edge-matched/open/intl/"
    "adm3_polygons.parquet"
)

# Network-type DuckDB errors are retried; SQL errors are not.
_RETRYABLE = tuple(
    getattr(duckdb, name)
    for name in ("IOException", "HTTPException")
    if hasattr(duckdb, name)
)


def _connect():
    """Create a DuckDB connection with Spatial enabled."""
    con = duckdb.connect()
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")

    # Reuse file metadata across the many requests made to one big file.
    try:
        con.execute("SET enable_http_metadata_cache = true")
    except duckdb.Error:
        pass  # older DuckDB without this setting

    return con


def _run_query(sql, parameters, retries=3):
    """Run a query on a fresh connection, retrying transient network errors."""

    last_error = None

    for attempt in range(1, retries + 1):
        con = None
        try:
            con = _connect()
            return con.execute(sql, parameters).fetchall()
        except _RETRYABLE as exc:
            last_error = exc
            time.sleep(2 * attempt)
        finally:
            if con is not None:
                con.close()

    raise RuntimeError(
        f"Could not read the boundary data after {retries} attempts: "
        f"{last_error}"
    ) from last_error


@lru_cache(maxsize=64)
def _admin1_names(country_code):
    rows = _run_query(
        """
        SELECT DISTINCT adm1_name
        FROM read_parquet(?)
        WHERE adm0_src = ?
          AND adm1_name IS NOT NULL
        ORDER BY adm1_name
        """,
        [BOUNDARIES_URL, country_code],
    )
    return tuple(row[0] for row in rows)


@lru_cache(maxsize=256)
def _admin2_names(admin1_name, country_code):
    rows = _run_query(
        """
        SELECT DISTINCT adm2_name
        FROM read_parquet(?)
        WHERE adm0_src = ?
          AND adm1_name = ?
          AND adm2_name IS NOT NULL
        ORDER BY adm2_name
        """,
        [BOUNDARIES_URL, country_code, admin1_name],
    )
    return tuple(row[0] for row in rows)


@lru_cache(maxsize=1024)
def _admin3_names(admin1_name, admin2_name, country_code):
    rows = _run_query(
        """
        SELECT DISTINCT adm3_name
        FROM read_parquet(?)
        WHERE adm0_src = ?
          AND adm1_name = ?
          AND adm2_name = ?
          AND adm3_name IS NOT NULL
        ORDER BY adm3_name
        """,
        [BOUNDARIES_URL, country_code, admin1_name, admin2_name],
    )
    return tuple(row[0] for row in rows)


# Public listing functions return a fresh list each call, so callers can
# modify the result without corrupting the cache.

def list_admin1(country_code="KEN"):
    """List Admin 1 areas, such as counties."""
    return list(_admin1_names(country_code))


def list_admin2(admin1_name, country_code="KEN"):
    """List Admin 2 areas within a selected Admin 1 area."""
    return list(_admin2_names(admin1_name, country_code))


def list_admin3(admin1_name, admin2_name, country_code="KEN"):
    """List Admin 3 areas within a selected Admin 2 area."""
    return list(_admin3_names(admin1_name, admin2_name, country_code))


def _polygonal_parts(geometry):
    """Collect every Polygon inside any geometry or collection."""

    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]

    parts = []

    for part in getattr(geometry, "geoms", []):
        parts.extend(_polygonal_parts(part))

    return parts


def clean_geometry(geometry):
    """
    Return a valid Polygon or MultiPolygon.

    Unioning many ward polygons can leave invalid slivers. Repair them with
    make_valid and drop any non-polygon fragments (stray lines or points).
    """

    if geometry is None or geometry.is_empty:
        raise ValueError(
            "The selected administrative geometry is empty."
        )

    if not geometry.is_valid:
        geometry = make_valid(geometry)

    polygons = _polygonal_parts(geometry)

    if not polygons:
        raise ValueError(
            "The selected administrative geometry has no polygon area."
        )

    cleaned = unary_union(polygons)

    if not isinstance(cleaned, (Polygon, MultiPolygon)):
        raise ValueError(
            "The selected administrative geometry could not be "
            "converted to a polygon."
        )

    if not cleaned.is_valid:
        raise ValueError(
            "The selected administrative geometry is invalid and could "
            "not be repaired."
        )

    return cleaned


def get_admin_geometry(
    level,
    country_code="KEN",
    admin1_name=None,
    admin2_name=None,
    admin3_name=None,
):
    """
    Return a Shapely geometry for a selected administrative area.

    Level 1: union all Admin 3 polygons within the selected Admin 1 area.
    Level 2: union all Admin 3 polygons within the selected Admin 2 area.
    Level 3: union polygons belonging to the selected Admin 3 area.
    """
    if level not in (1, 2, 3):
        raise ValueError("Administrative level must be 1, 2, or 3.")

    if not admin1_name:
        raise ValueError("Select an Admin 1 area first.")

    if level >= 2 and not admin2_name:
        raise ValueError("Select an Admin 2 area first.")

    if level == 3 and not admin3_name:
        raise ValueError("Select an Admin 3 area first.")

    conditions = [
        "adm0_src = ?",
        "adm1_name = ?",
        "geometry IS NOT NULL",
    ]
    parameters = [BOUNDARIES_URL, country_code, admin1_name]

    if level >= 2:
        conditions.append("adm2_name = ?")
        parameters.append(admin2_name)

    if level == 3:
        conditions.append("adm3_name = ?")
        parameters.append(admin3_name)

    sql = f"""
        SELECT ST_AsWKB(geometry)
        FROM read_parquet(?)
        WHERE {" AND ".join(conditions)}
    """

    rows = _run_query(sql, parameters)

    polygons = [
        wkb.loads(bytes(row[0]))
        for row in rows
        if row[0] is not None
    ]

    if not polygons:
        raise ValueError(
            "No boundary geometry found for the selected administrative area."
        )

    return clean_geometry(unary_union(polygons))


# Backward-compatible helpers for existing tests or code that selects a county.
def list_counties(country_code="KEN"):
    """List counties using the Admin 1 level."""
    return list_admin1(country_code)


def get_county_geometry(county_name, country_code="KEN"):
    """Get a county geometry using the Admin 1 level."""
    return get_admin_geometry(
        level=1,
        country_code=country_code,
        admin1_name=county_name,
    )