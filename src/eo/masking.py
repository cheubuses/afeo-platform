"""
Sentinel-2 Scene Classification Layer (SCL) masking.

Default retained classes:
    4  Vegetation
    5  Non-vegetated land
    6  Water

Default excluded classes:
    0  No data
    1  Saturated or defective pixels
    2  Topographic cast shadows / dark-area pixels
    3  Cloud shadows
    7  Unclassified (may include dark soils and burned areas)
    8  Medium-probability clouds
    9  High-probability clouds
    10 Cirrus
    11 Snow or ice

Class 2 is excluded by default to reduce shadow contamination.
Class 7 is excluded by default because its classification is uncertain.
For burn-scar products (NBR, NBR2) pass retained_classes=(4, 5, 6, 7),
because burned areas are often labelled class 7.
"""

import dask
import numpy as np
import xarray as xr


SCL_CLASS_NAMES = {
    0: "No data",
    1: "Saturated or defective",
    2: "Topographic cast shadows / dark-area pixels",
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

ALL_SCL_CLASSES = frozenset(SCL_CLASS_NAMES)

DEFAULT_RETAINED_SCL_CLASSES = (4, 5, 6)

# Public constants read by eo.tiled_pipeline for metadata.
RETAINED_SCL_CLASSES = DEFAULT_RETAINED_SCL_CLASSES
EXCLUDED_SCL_CLASSES = frozenset(ALL_SCL_CLASSES - set(RETAINED_SCL_CLASSES))

# Backward-compatible alias.
SCL_EXCLUDED_CLASSES = EXCLUDED_SCL_CLASSES

SPECTRAL_BANDS = ("red", "green", "blue", "nir")
OPTIONAL_SPECTRAL_BANDS = ("swir16", "swir22")


def normalize_retained_classes(retained_classes=None):
    """Validate and return the retained SCL classes as a sorted tuple."""

    if retained_classes is None:
        return DEFAULT_RETAINED_SCL_CLASSES

    classes = sorted({int(c) for c in retained_classes})

    if not classes:
        raise ValueError("retained_classes must not be empty.")

    unknown = [c for c in classes if c not in ALL_SCL_CLASSES]
    if unknown:
        raise ValueError(f"Unknown SCL classes: {unknown}")

    if 0 in classes:
        raise ValueError("SCL class 0 (No data) can never be retained.")

    return tuple(classes)


def excluded_classes_for(retained_classes=None):
    retained = normalize_retained_classes(retained_classes)
    return sorted(ALL_SCL_CLASSES - set(retained))


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
            if not np.array_equal(scl[dim].values, band[dim].values):
                raise ValueError(
                    f"SCL and {band_name} have different "
                    f"coordinates along {dim!r}."
                )

    try:
        scl_crs = scl.rio.crs
        band_crs = band.rio.crs

        if scl_crs is not None and band_crs is not None:
            if scl_crs != band_crs:
                raise ValueError(
                    f"SCL CRS {scl_crs} differs from "
                    f"{band_name} CRS {band_crs}."
                )

        if not np.allclose(
            tuple(scl.rio.transform()),
            tuple(band.rio.transform()),
        ):
            raise ValueError(
                f"SCL spatial transform differs from {band_name}."
            )
    except AttributeError:
        # No rio accessor: dimension and coordinate checks still apply.
        pass

    return True


def mask_with_scl(data, retained_classes=None):
    """
    Mask spectral bands using the Sentinel-2 Scene Classification Layer.

    Required bands:
        red, green, blue, nir, scl

    Optional bands:
        swir16, swir22

    retained_classes: SCL classes to keep (default 4, 5, 6).
    Pixels of any other class are set to NaN in every spectral band.
    """

    retained = normalize_retained_classes(retained_classes)
    excluded = excluded_classes_for(retained)

    if "scl" not in data.data_vars:
        raise ValueError(
            "Cannot apply SCL masking: dataset has no 'scl' band."
        )

    missing = [b for b in SPECTRAL_BANDS if b not in data.data_vars]

    if missing:
        raise ValueError(f"Missing required spectral bands: {missing}")

    bands_to_mask = list(SPECTRAL_BANDS) + [
        band for band in OPTIONAL_SPECTRAL_BANDS if band in data.data_vars
    ]

    for band_name in bands_to_mask:
        _check_scl_alignment(data, band_name)

    scl = data["scl"]

    # isin() is False for NaN and for any class not retained.
    valid_scl = scl.isin(list(retained))

    output = data.copy()

    for band_name in bands_to_mask:
        output[band_name] = data[band_name].where(valid_scl)

    output.attrs = dict(data.attrs)
    output.attrs["scl_mask_excluded_classes"] = excluded
    output.attrs["scl_mask_retained_classes"] = list(retained)
    output.attrs["scl_class_2_policy"] = (
        "Retained." if 2 in retained else "Excluded from the composite."
    )
    output.attrs["scl_class_7_policy"] = (
        "Retained (may include burned areas)."
        if 7 in retained
        else "Excluded because the class is unclassified."
    )

    return output


def count_valid_observations(masked, bands=None):
    """
    Lazily count, per pixel, the observations where every band is valid.

    Uses the already-masked dataset, so an observation counts only if it
    passed the SCL mask and is finite in all the given bands. Returns None
    when there is no time dimension. Nothing is computed here.
    """

    if bands is None:
        bands = [
            b
            for b in (*SPECTRAL_BANDS, *OPTIONAL_SPECTRAL_BANDS)
            if b in masked.data_vars
        ]

    if not bands:
        raise ValueError("No spectral bands available to count.")

    first = masked[bands[0]]

    if "time" not in first.dims:
        return None

    valid = xr.ones_like(first, dtype=bool)

    for band_name in bands:
        valid = valid & masked[band_name].notnull()

    count = valid.sum(dim="time").astype("uint16")
    count = count.rename("valid_observation_count")
    count.attrs["long_name"] = "Number of SCL-valid spectral observations"
    count.attrs["description"] = (
        "Counts observations that passed the SCL mask and are finite "
        f"in all of: {', '.join(bands)}."
    )

    return count


def calculate_masking_statistics(
    original,
    masked,
    spectral_bands=SPECTRAL_BANDS,
    retained_classes=None,
):
    """
    Calculate masking statistics over the time dimension.

    WARNING: this calls dask.compute on whole-dataset sums. Use it for
    small areas or a single tile, not for a whole large AOI.

    Returns
    -------
    stats : dict
        Summary statistics for metadata/reporting.
    valid_count : xarray.DataArray or None
        Number of valid observations per pixel. None if there
        is no time dimension.
    """

    retained = normalize_retained_classes(retained_classes)

    if "scl" not in original:
        raise ValueError("Original dataset has no SCL band.")

    scl = original["scl"]

    spectral_valid = xr.ones_like(scl, dtype=bool)

    for band_name in spectral_bands:
        spectral_valid = spectral_valid & np.isfinite(original[band_name])

    eligible = (
        spectral_valid
        & scl.notnull()
        & (scl != 0)
        & scl.isin(list(SCL_CLASS_NAMES))
    )

    accepted = eligible & scl.isin(list(retained))

    eligible_total, accepted_total = dask.compute(
        eligible.sum(),
        accepted.sum(),
    )

    eligible_count = int(eligible_total.item())
    accepted_count = int(accepted_total.item())
    rejected_count = eligible_count - accepted_count

    masked_percentage = (
        100.0 * rejected_count / eligible_count if eligible_count else 0.0
    )

    valid_count = None

    if "time" in scl.dims:
        valid_count = accepted.sum(dim="time").astype("uint16")
        valid_count = valid_count.rename("valid_observation_count")
        valid_count.attrs["long_name"] = (
            "Number of SCL-valid spectral observations"
        )
        valid_count.attrs["description"] = (
            f"Counts observations with SCL class in {list(retained)} "
            "and finite values in all required spectral bands."
        )

    stats = {
        "eligible_observations": eligible_count,
        "valid_observations_after_masking": accepted_count,
        "observations_rejected_by_scl": rejected_count,
        "masked_percentage": round(masked_percentage, 2),
        "excluded_scl_classes": excluded_classes_for(retained),
        "retained_scl_classes": list(retained),
        "class_2_excluded": 2 not in retained,
        "class_7_excluded": 7 not in retained,
    }

    return stats, valid_count