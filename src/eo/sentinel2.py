"""Sentinel-2 search, scene selection and loading (Earth Search, C1 L2A)."""

from odc.stac import stac_load
from shapely.geometry import mapping, shape


COLLECTION = "sentinel-2-c1-l2a"


def _polygon_vertex_count(geom):
    parts = getattr(geom, "geoms", [geom])
    total = 0
    for part in parts:
        if hasattr(part, "exterior"):
            total += len(part.exterior.coords)
            total += sum(len(ring.coords) for ring in part.interiors)
    return total


def _search_geometry(geometry, max_vertices=1500):
    """
    Return a lighter geometry for the STAC request.

    Large administrative polygons can have tens of thousands of vertices,
    which makes the search request huge. The result is buffered before it is
    simplified, so it always covers the original area; any extra scenes are
    discarded later by the per-tile footprint check and the exact AOI clip.
    """
    geom = shape(geometry) if isinstance(geometry, dict) else geometry

    if _polygon_vertex_count(geom) <= max_vertices:
        return mapping(geom)

    tolerance = 0.001  # degrees, roughly 100 m

    while tolerance <= 0.1:
        lighter = geom.buffer(tolerance).simplify(
            tolerance, preserve_topology=True
        )
        if _polygon_vertex_count(lighter) <= max_vertices:
            return mapping(lighter)
        tolerance *= 2

    # Last resort: the bounding box always covers the AOI.
    return mapping(geom.envelope)


def search_sentinel2(
    catalog,
    geometry,
    start_date,
    end_date,
    cloud_cover=30,
    page_size=200,
):
    """Search Earth Search for Sentinel-2 L2A imagery."""

    search = catalog.search(
        collections=[COLLECTION],
        intersects=_search_geometry(geometry),
        datetime=f"{start_date}/{end_date}",
        query={"eo:cloud_cover": {"lt": cloud_cover}},
        limit=page_size,
    )

    items = search.item_collection()

    if len(items) == 0:
        raise RuntimeError("No Sentinel-2 scenes were found.")

    return items


def select_best_scene(items):
    """
    Select the scene with the lowest reported cloud cover.

    eo:cloud_cover is a percentage (0-100).
    """

    if not items:
        raise RuntimeError(
            "Cannot select a scene: no Sentinel-2 items were supplied."
        )

    best_item = min(
        items,
        key=lambda item: item.properties.get("eo:cloud_cover", 100),
    )

    cloud_percent = best_item.properties.get("eo:cloud_cover")

    print(f"Selected scene: {best_item.id}")

    if cloud_percent is not None:
        print(f"Scene cloud cover: {cloud_percent:.4f}%")
    else:
        print("Scene cloud cover: not reported")

    return best_item


def load_sentinel2_items(
    items,
    geometry,
    requested_product="RGB",
    *,
    tile_bounds=None,
    target_crs=None,
):
    """
    Load Sentinel-2 bands for the requested product.

    Without tile_bounds, load the whole AOI (geometry required).
    With tile_bounds, load one projected tile; tile bounds must be
    multiples of the resolution so neighbouring tiles share one pixel grid.
    """

    product = requested_product.strip().upper()

    bands = ["red", "green", "blue", "nir", "scl"]

    if product == "NDMI":
        bands.append("swir16")
    elif product == "NBR":
        bands.append("swir22")
    elif product == "NBR2":
        bands.extend(["swir16", "swir22"])

    resolution = 20 if product in {"NDMI", "NBR", "NBR2"} else 10

    # 10 m bands are shrunk when loaded at 20 m, so average rather than
    # point-sample. SCL is categorical and must stay nearest-neighbour.
    continuous_resampling = "average" if resolution == 20 else "bilinear"

    load_options = {
        "bands": bands,
        "chunks": {"x": 1024, "y": 1024, "time": 1},
        "groupby": "solar_day",
        "resampling": {
            band: (
                "nearest" if band == "scl" else continuous_resampling
            )
            for band in bands
        },
    }

    if tile_bounds is None:
        if geometry is None:
            raise ValueError(
                "A geometry is required for whole-AOI loading."
            )

        load_options["geopolygon"] = geometry

    else:
        if target_crs is None:
            raise ValueError(
                "target_crs is required when loading a tile."
            )

        xmin, ymin, xmax, ymax = tile_bounds

        load_options.update({
            "x": (xmin, xmax),
            "y": (ymin, ymax),
            "crs": target_crs,
            "resolution": resolution,
            "anchor": "edge",
        })

    return stac_load(list(items), **load_options)
