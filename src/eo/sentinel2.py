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
        collections=["sentinel-2-c1-l2a"],
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

    Without tile_bounds, retain the existing whole-AOI loading mode.
    With tile_bounds, load one projected tile at a time.
    """
    from odc.stac import stac_load

    product = requested_product.strip().upper()

    bands = ["red", "green", "blue", "nir", "scl"]

    if product == "NDMI":
        bands.append("swir16")
    elif product == "NBR":
        bands.append("swir22")
    elif product == "NBR2":
        bands.extend(["swir16", "swir22"])

    resolution = (
        20 if product in {"NDMI", "NBR", "NBR2"} else 10
    )

    load_options = {
        "bands": bands,
        "chunks": {"x": 1024, "y": 1024},
        "groupby": "solar_day",
        "resampling": {
            band: (
                "nearest" if band == "scl" else "bilinear"
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