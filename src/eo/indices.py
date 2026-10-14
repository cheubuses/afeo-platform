
import numpy as np
import xarray as xr


def calculate_ndvi(
    red,
    nir,
    scale_factor=10000.0,
    red_offset=0.0,
    nir_offset=0.0,
):
    """
    Calculate NDVI from Sentinel-2 red and NIR surface reflectance.

    Parameters
    ----------
    red : xarray.DataArray
        Sentinel-2 Band 4 (red).
    nir : xarray.DataArray
        Sentinel-2 Band 8 (near infrared).
    scale_factor : float
        Reflectance quantification factor for DN inputs.
        Use 10000 for DN values stored at 10000 times reflectance.
        Use 1 for inputs already expressed as reflectance.
    red_offset, nir_offset : float
        Additive offsets in the source DN units, if applicable.
        Verify these against the source product metadata.

    Returns
    -------
    xarray.DataArray
        Floating-point NDVI. Invalid pixels and zero denominators
        are represented as NaN. Values are not clipped.
    """
    if scale_factor <= 0:
        raise ValueError("scale_factor must be greater than zero.")

    # Convert to float before arithmetic.
    red = red.astype("float32")
    nir = nir.astype("float32")

    # Convert source digital numbers to surface reflectance.
    red_reflectance = (red + red_offset) / scale_factor
    nir_reflectance = (nir + nir_offset) / scale_factor

    denominator = nir_reflectance + red_reflectance

    # Exclude invalid inputs and pixels where the denominator is zero.
    valid = (
        np.isfinite(red_reflectance)
        & np.isfinite(nir_reflectance)
        & np.isfinite(denominator)
        & (denominator != 0)
    )

    ndvi = xr.where(
        valid,
        (nir_reflectance - red_reflectance) / denominator,
        np.nan,
    )

    ndvi = ndvi.astype("float32")
    ndvi.name = "ndvi"
    ndvi.attrs.update({
        "long_name": "Normalized Difference Vegetation Index",
        "formula": "(NIR - Red) / (NIR + Red)",
        "red_band": "Sentinel-2 B04",
        "nir_band": "Sentinel-2 B08",
        "reflectance_scale_factor": float(scale_factor),
        "red_offset_dn": float(red_offset),
        "nir_offset_dn": float(nir_offset),
        "valid_range_expected": "approximately -1 to 1",
        "clipping_applied": False,
    })

    return ndvi

