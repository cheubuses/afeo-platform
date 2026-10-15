
import numpy as np
import xarray as xr



def calculate_ndvi(
    red,
    nir,
    scale_factor=1.0,
    red_offset=0.0,
    nir_offset=0.0,
    denominator_threshold=1e-6,
):
    """
    Calculate NDVI from Sentinel-2 red and NIR surface reflectance.

    Inputs should already be surface reflectance when scale_factor=1.0
    and both offsets are zero.

    Invalid inputs, non-positive reflectance and near-zero denominators
    are returned as NaN. NDVI values are not clipped.
    """

    if scale_factor <= 0:
        raise ValueError("scale_factor must be greater than zero.")

    if denominator_threshold <= 0:
        raise ValueError("denominator_threshold must be greater than zero.")

    red = red.astype("float32")
    nir = nir.astype("float32")

    red_reflectance = (red + red_offset) / scale_factor
    nir_reflectance = (nir + nir_offset) / scale_factor

    denominator = nir_reflectance + red_reflectance

    # Require finite, positive reflectance in both bands.
    # Reject denominators too close to zero.
    valid = (
        np.isfinite(red_reflectance)
        & np.isfinite(nir_reflectance)
        & (red_reflectance > 0)
        & (nir_reflectance > 0)
        & np.isfinite(denominator)
        & (denominator > denominator_threshold)
    )

    ndvi = xr.where(
        valid,
        (nir_reflectance - red_reflectance) / denominator,
        np.nan,
    ).astype("float32")

    ndvi.name = "ndvi"

    ndvi.attrs.update({
        "long_name": "Normalized Difference Vegetation Index",
        "formula": "(NIR - Red) / (NIR + Red)",
        "red_band": "Sentinel-2 B04",
        "nir_band": "Sentinel-2 B08",
        "reflectance_scale_factor": float(scale_factor),
        "red_offset_dn": float(red_offset),
        "nir_offset_dn": float(nir_offset),
        "denominator_threshold": float(denominator_threshold),
        "non_positive_reflectance_masked": True,
        "valid_range_expected": "-1 to 1",
        "clipping_applied": False,
    })

    return ndvi