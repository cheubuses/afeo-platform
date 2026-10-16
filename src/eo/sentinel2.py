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


def load_sentinel2_items(items, geometry, requested_product="RGB"):
    """
    Load Sentinel-2 bands needed for the requested EO product.

    RGB, false colour, NDVI and NDWI use the existing 10 m workflow.

    NDMI, NBR and NBR2 load the required SWIR bands on a 20 m grid.
    SCL is retained for quality masking.
    """

    product = requested_product.strip().upper()

    # Bands required by the existing RGB preview and masking workflow.
    bands = ["red", "green", "blue", "nir", "scl"]

    # Load only the additional SWIR bands required by the product.
    if product == "NDMI":
        bands.append("swir16")

    elif product == "NBR":
        bands.append("swir22")

    elif product == "NBR2":
        bands.extend(["swir16", "swir22"])

    # Keep the existing loading behaviour for products using
    # only the standard visible/NIR bands.
    load_options = {}

    # Use a 20 m output grid for indices requiring SWIR.
    if product in {"NDMI", "NBR", "NBR2"}:
        load_options["resolution"] = 20

        # Continuous reflectance bands use bilinear resampling.
        # SCL is categorical, so it must use nearest-neighbour.
        load_options["resampling"] = {
            band: ("nearest" if band == "scl" else "bilinear")
            for band in bands
        }

    data = stac_load(
        list(items),
        bands=bands,
        geopolygon=geometry,
        chunks={"x": 1024, "y": 1024},
        groupby="solar_day",
        **load_options,
    )

    return data


def load_sentinel2_items(items, geometry, requested_product="RGB"):
    """
    Load Sentinel-2 bands required for the requested product.

    Spatial chunks are 1024 x 1024.
    SWIR-based indices use a 20 m output grid.
    """

    product = requested_product.strip().upper()

    # Keep the core bands for RGB previews and SCL masking.
    bands = ["red", "green", "blue", "nir", "scl"]

    if product == "NDMI":
        bands.append("swir16")

    elif product == "NBR":
        bands.append("swir22")

    elif product == "NBR2":
        bands.extend(["swir16", "swir22"])

    load_options = {
        "chunks": {"x": 1024, "y": 1024},
        "groupby": "solar_day",
    }

    # SWIR bands are native 20 m, so use a 20 m grid
    # for the moisture and burn indices.
    if product in {"NDMI", "NBR", "NBR2"}:
        load_options["resolution"] = 20
        load_options["resampling"] = {
            band: (
                "nearest" if band == "scl" else "bilinear"
            )
            for band in bands
        }

    data = stac_load(
        list(items),
        bands=bands,
        geopolygon=geometry,
        **load_options,
    )

    return data