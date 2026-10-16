
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

# ============================================================
# REUSABLE NORMALIZED DIFFERENCE INDEX CALCULATIONS
# ============================================================

def _calculate_normalized_difference(
    band_a,
    band_b,
    *,
    index_name,
    long_name,
    formula,
    band_a_name,
    band_b_name,
    denominator_threshold=1e-6,
):
    """
    Calculate a normalized difference index from reflectance bands.

    Input bands must already be converted to surface reflectance.
    No additional scale factor or offset is applied.
    """

    if denominator_threshold <= 0:
        raise ValueError(
            "denominator_threshold must be greater than zero."
        )

    band_a = band_a.astype("float32")
    band_b = band_b.astype("float32")

    denominator = band_a + band_b

    valid = (
        np.isfinite(band_a)
        & np.isfinite(band_b)
        & (band_a > 0)
        & (band_b > 0)
        & np.isfinite(denominator)
        & (np.abs(denominator) > denominator_threshold)
    )

    result = xr.where(
        valid,
        (band_a - band_b) / denominator,
        np.nan,
    ).astype("float32")

    result.name = index_name

    result.attrs.update({
        "long_name": long_name,
        "formula": formula,
        "band_a": band_a_name,
        "band_b": band_b_name,
        "input_units": "surface reflectance",
        "denominator_threshold": float(denominator_threshold),
        "non_positive_reflectance_masked": True,
        "expected_range": "-1 to 1",
        "clipping_applied": False,
    })

    return result


def calculate_ndwi(green, nir):
    """ Normalized Differential Water Index - NDWI for open-water enhancement."""
    return _calculate_normalized_difference(
        green,
        nir,
        index_name="ndwi",
        long_name="Normalized Difference Water Index (McFeeters)",
        formula="(Green - NIR) / (Green + NIR)",
        band_a_name="Sentinel-2 B03 (Green)",
        band_b_name="Sentinel-2 B08 (NIR)",
    )


def calculate_ndmi(nir, swir1):
    """Normalized Difference Moisture Index."""
    return _calculate_normalized_difference(
        nir,
        swir1,
        index_name="ndmi",
        long_name="Normalized Difference Moisture Index",
        formula="(NIR - SWIR1) / (NIR + SWIR1)",
        band_a_name="Sentinel-2 B08 (NIR)",
        band_b_name="Sentinel-2 B11 (SWIR1)",
    )


def calculate_nbr(nir, swir2):
    """Normalized Burn Ratio."""
    return _calculate_normalized_difference(
        nir,
        swir2,
        index_name="nbr",
        long_name="Normalized Burn Ratio",
        formula="(NIR - SWIR2) / (NIR + SWIR2)",
        band_a_name="Sentinel-2 B08 (NIR)",
        band_b_name="Sentinel-2 B12 (SWIR2)",
    )


def calculate_nbr2(swir1, swir2):
    """Normalized Burn Ratio 2."""
    return _calculate_normalized_difference(
        swir1,
        swir2,
        index_name="nbr2",
        long_name="Normalized Burn Ratio 2",
        formula="(SWIR1 - SWIR2) / (SWIR1 + SWIR2)",
        band_a_name="Sentinel-2 B11 (SWIR1)",
        band_b_name="Sentinel-2 B12 (SWIR2)",
    )