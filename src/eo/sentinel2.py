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
    Select the Sentinel-2 scene with the lowest
    reported cloud cover.

    eo:cloud_cover is expressed as a percentage (0–100).
    """

    if not items:
        raise RuntimeError(
            "Cannot select a scene: no Sentinel-2 items were supplied."
        )

    best_item = min(
        items,
        key=lambda item: item.properties.get(
            "eo:cloud_cover",
            100
        )
    )

    cloud_percent = best_item.properties.get(
        "eo:cloud_cover",
        None
    )

    print(f"Selected scene: {best_item.id}")

    if cloud_percent is not None:
        print(f"Scene cloud cover: {cloud_percent:.4f}%")
    else:
        print("Scene cloud cover: not reported")

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
    list(items),
    bands=["red", "green", "blue", "nir", "scl"],
    geopolygon=geometry,
    chunks={"x": 1024, "y": 1024},
    groupby="solar_day"
),


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