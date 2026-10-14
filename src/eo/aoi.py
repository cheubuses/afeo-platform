"""
Administrative Area of Interest (AOI) selection.

Reads FieldMaps administrative boundaries directly from remote GeoParquet
using DuckDB Spatial.

Admin 1: County, e.g. Baringo
Admin 2: Sub-county, e.g. Baringo Central
Admin 3: Ward, e.g. Ewalel Chapchap

Geometry coordinates are longitude, latitude (OGC:CRS84).
"""

from functools import lru_cache

import duckdb
from shapely import wkb
from shapely.ops import unary_union


BOUNDARIES_URL = (
    "https://data.fieldmaps.io/edge-matched/open/intl/"
    "adm3_polygons.parquet"
)


def _connect():
    """Create a DuckDB connection with Spatial enabled."""
    con = duckdb.connect()
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    return con


@lru_cache(maxsize=1)
def list_admin1(country_code="KEN"):
    """List Admin 1 areas, such as counties."""
    con = _connect()
    try:
        rows = con.execute(
            """
            SELECT DISTINCT adm1_name
            FROM read_parquet(?)
            WHERE adm0_src = ?
              AND adm1_name IS NOT NULL
            ORDER BY adm1_name
            """,
            [BOUNDARIES_URL, country_code],
        ).fetchall()

        return [row[0] for row in rows]
    finally:
        con.close()


def list_admin2(admin1_name, country_code="KEN"):
    """List Admin 2 areas within a selected Admin 1 area."""
    con = _connect()
    try:
        rows = con.execute(
            """
            SELECT DISTINCT adm2_name
            FROM read_parquet(?)
            WHERE adm0_src = ?
              AND adm1_name = ?
              AND adm2_name IS NOT NULL
            ORDER BY adm2_name
            """,
            [BOUNDARIES_URL, country_code, admin1_name],
        ).fetchall()

        return [row[0] for row in rows]
    finally:
        con.close()


def list_admin3(admin1_name, admin2_name, country_code="KEN"):
    """List Admin 3 areas within a selected Admin 2 area."""
    con = _connect()
    try:
        rows = con.execute(
            """
            SELECT DISTINCT adm3_name
            FROM read_parquet(?)
            WHERE adm0_src = ?
              AND adm1_name = ?
              AND adm2_name = ?
              AND adm3_name IS NOT NULL
            ORDER BY adm3_name
            """,
            [
                BOUNDARIES_URL,
                country_code,
                admin1_name,
                admin2_name,
            ],
        ).fetchall()

        return [row[0] for row in rows]
    finally:
        con.close()


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

    con = _connect()
    try:
        rows = con.execute(sql, parameters).fetchall()
    finally:
        con.close()

    polygons = [
        wkb.loads(bytes(row[0]))
        for row in rows
        if row[0] is not None
    ]

    if not polygons:
        raise ValueError(
            "No boundary geometry found for the selected administrative area."
        )

    geometry = unary_union(polygons)

    if geometry.is_empty or not geometry.is_valid:
        raise ValueError(
            "The selected administrative geometry is empty or invalid."
        )

    return geometry


# Backward-compatible helper for existing tests or code that selects a county.
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
