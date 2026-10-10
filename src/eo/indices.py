def calculate_ndvi(red, nir):
    """
    Calculate Normalized Difference Vegetation Index.

    NDVI = (NIR - Red) / (NIR + Red)
    """

    ndvi = (
        (nir - red)
        /
        (nir + red)
    )

    ndvi.name = "NDVI"

    return ndvi