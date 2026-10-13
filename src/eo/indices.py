
import numpy as np


def calculate_ndvi(red, nir):
    """Calculate NDVI while preserving invalid pixels as NoData."""

    # Identify missing and invalid reflectance values.
    valid = (
        red.notnull()
        & nir.notnull()
        & np.isfinite(red)
        & np.isfinite(nir)
        & (red >= 0)
        & (nir >= 0)
    )

    denominator = nir + red

    # Avoid division by zero and pixels where both bands are zero.
    valid = valid & (denominator > 0)

    ndvi = ((nir - red) / denominator).where(valid)

    # Preserve spatial metadata and identify the output band.
    ndvi.name = "NDVI"
    ndvi.attrs["long_name"] = "Normalized Difference Vegetation Index"
    ndvi.attrs["valid_min"] = -1.0
    ndvi.attrs["valid_max"] = 1.0
    ndvi.attrs["nodata_handling"] = (
        "Missing, non-finite, negative reflectance, "
        "and zero-denominator pixels are masked."
    )

    return ndvi
