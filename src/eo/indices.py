"""Normalized-difference spectral indices for Sentinel-2 reflectance."""

import numpy as np
import xarray as xr


NEGATIVE_POLICIES = {"clamp", "mask"}


def _normalized_difference(
    band_a,
    band_b,
    *,
    index_name,
    long_name,
    formula,
    band_a_name,
    band_b_name,
    denominator_threshold=1e-6,
    negative_reflectance="clamp",
    extra_attrs=None,
):
    """
    Calculate (A - B) / (A + B) from surface-reflectance bands.

    negative_reflectance:
      "clamp" (default): small negative reflectance, which is common over
          water and dark surfaces after the BOA offset, is set to 0. Water
          and shadow pixels therefore keep a valid index value.
      "mask": pixels where either band is <= 0 become NaN (strict mode).

    In both modes, non-finite inputs and near-zero denominators become NaN.
    The index itself is never clipped; with "clamp" it is mathematically
    confined to [-1, 1].
    """

    if denominator_threshold <= 0:
        raise ValueError("denominator_threshold must be greater than zero.")

    if negative_reflectance not in NEGATIVE_POLICIES:
        raise ValueError(
            f"negative_reflectance must be one of {sorted(NEGATIVE_POLICIES)}."
        )

    a = band_a.astype("float32")
    b = band_b.astype("float32")

    if negative_reflectance == "clamp":
        # clip() keeps NaN as NaN, so NoData is preserved.
        a = a.clip(min=0)
        b = b.clip(min=0)
        positive = True
    else:
        positive = (a > 0) & (b > 0)

    denominator = a + b

    valid = (
        np.isfinite(a)
        & np.isfinite(b)
        & np.isfinite(denominator)
        & (np.abs(denominator) > denominator_threshold)
        & positive
    )

    result = xr.where(valid, (a - b) / denominator, np.nan).astype("float32")

    result.name = index_name

    attrs = {
        "long_name": long_name,
        "formula": formula,
        "band_a": band_a_name,
        "band_b": band_b_name,
        "input_units": "surface reflectance",
        "denominator_threshold": float(denominator_threshold),
        "negative_reflectance_policy": negative_reflectance,
        "expected_range": "-1 to 1",
        "clipping_applied": False,
    }

    if extra_attrs:
        attrs.update(extra_attrs)

    result.attrs.update(attrs)

    return result


def calculate_ndvi(
    red,
    nir,
    scale_factor=1.0,
    red_offset=0.0,
    nir_offset=0.0,
    denominator_threshold=1e-6,
    negative_reflectance="clamp",
):
    """
    Normalized Difference Vegetation Index.

    Inputs should already be surface reflectance when scale_factor=1.0
    and both offsets are zero. A non-default scale_factor or offsets are
    applied here as (DN + offset) / scale_factor.
    """

    if scale_factor <= 0:
        raise ValueError("scale_factor must be greater than zero.")

    red = (red.astype("float32") + red_offset) / scale_factor
    nir = (nir.astype("float32") + nir_offset) / scale_factor

    return _normalized_difference(
        nir,
        red,
        index_name="ndvi",
        long_name="Normalized Difference Vegetation Index",
        formula="(NIR - Red) / (NIR + Red)",
        band_a_name="Sentinel-2 B08 (NIR)",
        band_b_name="Sentinel-2 B04 (Red)",
        denominator_threshold=denominator_threshold,
        negative_reflectance=negative_reflectance,
        extra_attrs={
            "reflectance_scale_factor": float(scale_factor),
            "red_offset_dn": float(red_offset),
            "nir_offset_dn": float(nir_offset),
        },
    )


def calculate_ndwi(green, nir, **kwargs):
    """Normalized Difference Water Index (McFeeters), open-water enhancement."""
    return _normalized_difference(
        green,
        nir,
        index_name="ndwi",
        long_name="Normalized Difference Water Index (McFeeters)",
        formula="(Green - NIR) / (Green + NIR)",
        band_a_name="Sentinel-2 B03 (Green)",
        band_b_name="Sentinel-2 B08 (NIR)",
        **kwargs,
    )


def calculate_ndmi(nir, swir1, **kwargs):
    """Normalized Difference Moisture Index."""
    return _normalized_difference(
        nir,
        swir1,
        index_name="ndmi",
        long_name="Normalized Difference Moisture Index",
        formula="(NIR - SWIR1) / (NIR + SWIR1)",
        band_a_name="Sentinel-2 B08 (NIR)",
        band_b_name="Sentinel-2 B11 (SWIR1)",
        **kwargs,
    )


def calculate_nbr(nir, swir2, **kwargs):
    """Normalized Burn Ratio."""
    return _normalized_difference(
        nir,
        swir2,
        index_name="nbr",
        long_name="Normalized Burn Ratio",
        formula="(NIR - SWIR2) / (NIR + SWIR2)",
        band_a_name="Sentinel-2 B08 (NIR)",
        band_b_name="Sentinel-2 B12 (SWIR2)",
        **kwargs,
    )


def calculate_nbr2(swir1, swir2, **kwargs):
    """Normalized Burn Ratio 2."""
    return _normalized_difference(
        swir1,
        swir2,
        index_name="nbr2",
        long_name="Normalized Burn Ratio 2",
        formula="(SWIR1 - SWIR2) / (SWIR1 + SWIR2)",
        band_a_name="Sentinel-2 B11 (SWIR1)",
        band_b_name="Sentinel-2 B12 (SWIR2)",
        **kwargs,
    )