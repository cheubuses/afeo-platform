from odc.stac import stac_load


def search_sentinel2(
    catalog,
    geometry,
    start_date,
    end_date,
    cloud_cover=30
):
    """
    Search Earth Search for Sentinel-2 L2A imagery.
    """

    search = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects=geometry,
        datetime=f"{start_date}/{end_date}",
        query={
            "eo:cloud_cover": {
                "lt": cloud_cover
            }
        }
    )

    items = search.item_collection()

    if len(items) == 0:
        raise RuntimeError(
            "No Sentinel-2 scenes were found."
        )

    return items


def select_best_scene(items):
    """
    Select the Sentinel-2 scene with
    the lowest cloud cover.
    """

    best_item = min(
        items,
        key=lambda item: item.properties.get(
            "eo:cloud_cover",
            100
        )
    )

    return best_item


def load_sentinel2(
    item,
    geometry
):
    """
    Load Sentinel-2 bands as a lazy
    XArray dataset using Dask.
    """

    data = stac_load(
        [item],
        bands=[
            "red",
            "green",
            "blue",
            "nir"
        ],
        geopolygon=geometry,
        chunks={
            "x": 512,
            "y": 512
        }
    )

    return data


def load_sentinel2_items(items, geometry):
    """
    Load multiple Sentinel-2 scenes for temporal compositing.
    Data are loaded lazily using Dask.
    """

    data = stac_load(
        list(items),
        bands=["red", "green", "blue", "nir", "scl"],
        geopolygon=geometry,
        chunks={"x": 512, "y": 512},
        groupby="solar_day"
    )

    return data