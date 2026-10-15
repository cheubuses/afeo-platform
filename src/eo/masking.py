"""
Sentinel-2 Scene Classification Layer (SCL) masking.

Excluded classes:
    0  No data
    1  Saturated or defective pixels
    2  Cast shadows
    3  Cloud shadows
    7  Unclassified (may include dark soils and burned areas)
    8  Medium-probability clouds
    9  High-probability clouds
    10 Cirrus
    11 Snow or ice

Retained classes:
    4  Vegetation
    5  Non-vegetated land
    6  Water

Class 2 is excluded to reduce shadow contamination.
Class 7 is excluded because its classification is uncertain.
"""
import numpy as np
import xarray as xr
import dask


SCL_CLASS_NAMES = {
    0: "No data",
    1: "Saturated or defective",
    2: "Dark-area pixels",
    3: "Cloud shadows",
    4: "Vegetation",
    5: "Non-vegetated land",
    6: "Water",
    7: "Unclassified",
    8: "Medium-probability clouds",
    9: "High-probability clouds",
    10: "Cirrus",
    11: "Snow or ice",
}

SCL_EXCLUDED_CLASSES = frozenset(
    {0, 1, 2, 3, 7, 8, 9, 10, 11}
)

SPECTRAL_BANDS = ("red", "green", "blue", "nir")


def _check_scl_alignment(data, band_name):
    """Check SCL grid alignment against a spectral band."""

    scl = data["scl"]
    band = data[band_name]

    if scl.dims != band.dims:
        raise ValueError(
            f"SCL dimensions {scl.dims} do not match "
            f"{band_name} dimensions {band.dims}."
        )

    if scl.shape != band.shape:
        raise ValueError(
            f"SCL shape {scl.shape} does not match "
            f"{band_name} shape {band.shape}."
        )

    for dim in scl.dims:
        if dim in scl.coords and dim in band.coords:
            if not np.array_equal(
                scl[dim].values, band[dim].values
            ):
                raise ValueError(
                    f"SCL and {band_name} have different "
                    f"coordinates along {dim!r}."
                )

    # Compare spatial CRS and transforms when available.
    try:
        scl_crs = scl.rio.crs
        band_crs = band.rio.crs
        if scl_crs is not None and band_crs is not None:
            if scl_crs != band_crs:
                raise ValueError(
                    f"SCL CRS {scl_crs} differs from "
                    f"{band_name} CRS {band_crs}."
                )

        scl_transform = scl.rio.transform()
        band_transform = band.rio.transform()

        if not np.allclose(
            tuple(scl_transform),
            tuple(band_transform),
        ):
            raise ValueError(
                f"SCL spatial transform differs from "
                f"{band_name}."
            )
    except AttributeError:
        # If the rio accessor is unavailable, dimension and
        # coordinate checks still apply. The caller should
        # verify spatial metadata separately.
        pass

    return True


def mask_with_scl(data):
    """
    Mask red, green, blue and NIR using Sentinel-2 SCL.

    Parameters
    ----------
    data : xarray.Dataset
        Must contain red, green, blue, nir and scl variables.

    Returns
    -------
    xarray.Dataset
        Dataset with excluded spectral pixels set to NaN.
        The SCL variable is retained for diagnostics.
    """

    if "scl" not in data:
        raise ValueError(
            "Cannot apply SCL masking: dataset has no 'scl' band. "
            "Ensure load_sentinel2_items loads bands including 'scl'."
        )

    missing = [
        band for band in SPECTRAL_BANDS
        if band not in data.data_vars
    ]
    if missing:
        raise ValueError(
            f"Missing required spectral bands: {missing}"
        )

    # Verify grid alignment before constructing the mask.
    for band_name in SPECTRAL_BANDS:
        _check_scl_alignment(data, band_name)

    scl = data["scl"]

    # Unknown SCL values are also excluded.
    known_class = scl.isin(list(SCL_CLASS_NAMES))
    clear_class = scl.isin([4, 5, 6])
    valid_scl = scl.notnull() & known_class & clear_class

    output = data.copy()

    for band_name in SPECTRAL_BANDS:
        output[band_name] = data[band_name].where(valid_scl)

    output.attrs = dict(data.attrs)
    output.attrs["scl_mask_excluded_classes"] = sorted(
        SCL_EXCLUDED_CLASSES
    )
    output.attrs["scl_mask_retained_classes"] = [4, 5, 6]
    output.attrs["scl_class_2_policy"] = (
        "Excluded conservatively; dark-area pixels may include "
        "genuine dark surfaces."
    )
    output.attrs["scl_class_7_policy"] = (
        "Excluded because unclassified pixels have uncertain "
        "surface conditions."
    )

    return output


def calculate_masking_statistics(
    original,
    masked,
    spectral_bands=SPECTRAL_BANDS,
):
    """
    Calculate masking statistics over the time dimension.

    Returns
    -------
    stats : dict
        Summary statistics for metadata/reporting.
    valid_count : xarray.DataArray or None
        Number of valid observations per pixel. None if there
        is no time dimension.
    """
    
    if "scl" not in original:
        raise ValueError("Original dataset has no SCL band.")

    scl = original["scl"]

    # A source observation is eligible when SCL is present,
    # the SCL class is not NoData, and all spectral bands
    # contain finite values.
    spectral_valid = xr.ones_like(scl, dtype=bool)

    for band_name in spectral_bands:
        spectral_valid = (
            spectral_valid
            & np.isfinite(original[band_name])
        )

    eligible = (
        spectral_valid
        & scl.notnull()
        & (scl != 0)
        & scl.isin(list(SCL_CLASS_NAMES))
    )

    accepted = (
        eligible
        & scl.isin([4, 5, 6])
    )

    

    eligible_total, accepted_total = dask.compute(
        eligible.sum(),
        accepted.sum(),
    )

    eligible_count = int(eligible_total.item())
    accepted_count = int(accepted_total.item())
    rejected_count = eligible_count - accepted_count

    masked_percentage = (
        100.0 * rejected_count / eligible_count
        if eligible_count else 0.0
    )

    # Count accepted observations independently at each pixel.
    valid_count = None
    if "time" in scl.dims:
        valid_count = accepted.sum(dim="time").astype("uint16")
        valid_count = valid_count.rename(
            "valid_observation_count"
        )
        valid_count.attrs["long_name"] = (
            "Number of SCL-valid spectral observations"
        )
        valid_count.attrs["description"] = (
            "Counts observations with SCL class 4, 5 or 6 "
            "and finite values in all required spectral bands."
        )


    stats = {
        "eligible_observations": eligible_count,
        "valid_observations_after_masking": accepted_count,
        "observations_rejected_by_scl": rejected_count,
        "masked_percentage": round(masked_percentage, 2),
        "excluded_scl_classes": sorted(SCL_EXCLUDED_CLASSES),
        "retained_scl_classes": [4, 5, 6],
        "class_2_excluded": True,
        "class_7_excluded": True,
    }

    return stats, valid_count
   