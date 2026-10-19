"""Dispatch the requested EO product calculation."""

from eo.indices import (
    calculate_ndvi,
    calculate_ndwi,
    calculate_ndmi,
    calculate_nbr,
    calculate_nbr2,
)
from eo.composites import create_rgb, create_false_color


# product -> (required bands, function taking the composite dataset)
PRODUCTS = {
    "RGB": (
        ["red", "green", "blue"],
        lambda d: create_rgb(d),
    ),
    "FALSE_COLOR": (
        ["nir", "red", "green"],
        lambda d: create_false_color(d),
    ),
    "NDVI": (
        ["red", "nir"],
        lambda d: calculate_ndvi(d["red"], d["nir"]),
    ),
    "NDWI": (
        ["green", "nir"],
        lambda d: calculate_ndwi(d["green"], d["nir"]),
    ),
    "NDMI": (
        ["nir", "swir16"],
        lambda d: calculate_ndmi(d["nir"], d["swir16"]),
    ),
    "NBR": (
        ["nir", "swir22"],
        lambda d: calculate_nbr(d["nir"], d["swir22"]),
    ),
    "NBR2": (
        ["swir16", "swir22"],
        lambda d: calculate_nbr2(d["swir16"], d["swir22"]),
    ),
}


def process_data(data, process):
    """Run the requested EO product calculation."""

    process = str(process).strip().upper()

    if process == "QUALITY_MASK":
        raise NotImplementedError(
            "QUALITY_MASK needs its own output path based on SCL "
            "and valid observation counts."
        )

    if process not in PRODUCTS:
        raise ValueError(f"Unsupported process: {process}")

    required, calculate = PRODUCTS[process]

    missing = [band for band in required if band not in data.data_vars]

    if missing:
        raise ValueError(
            f"{process} processing requires missing bands: {missing}. "
            "Check the Sentinel-2 loader and composite band selection."
        )

    return calculate(data)