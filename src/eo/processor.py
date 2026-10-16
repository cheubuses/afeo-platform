
from eo.indices import calculate_ndvi
from eo.composites import create_rgb, create_false_color

from eo.indices import (
    calculate_ndvi,
    calculate_ndwi,
    calculate_ndmi,
    calculate_nbr,
    calculate_nbr2,
)
from eo.composites import create_rgb, create_false_color


def process_data(data, process):
    """Run the requested EO product calculation."""

    process = str(process).strip().upper()

    required_bands = {
        "RGB": ["red", "green", "blue"],
        "FALSE_COLOR": ["nir", "red", "green"],
        "NDVI": ["red", "nir"],
        "NDWI": ["green", "nir"],
        "NDMI": ["nir", "swir16"],
        "NBR": ["nir", "swir22"],
        "NBR2": ["swir16", "swir22"],
    }

    if process == "QUALITY_MASK":
        raise NotImplementedError(
            "QUALITY_MASK needs its own output path based on SCL "
            "and valid observation counts."
        )

    if process not in required_bands:
        raise ValueError(f"Unsupported process: {process}")

    missing = [
        band for band in required_bands[process]
        if band not in data.data_vars
    ]

    if missing:
        raise ValueError(
            f"{process} processing requires missing bands: {missing}. "
            "Check the Sentinel-2 loader and composite band selection."
        )

    if process == "RGB":
        result = create_rgb(data)

    elif process == "FALSE_COLOR":
        result = create_false_color(data)

    elif process == "NDVI":
        result = calculate_ndvi(data["red"], data["nir"])

    elif process == "NDWI":
        result = calculate_ndwi(data["green"], data["nir"])

    elif process == "NDMI":
        result = calculate_ndmi(data["nir"], data["swir16"])

    elif process == "NBR":
        result = calculate_nbr(data["nir"], data["swir22"])

    elif process == "NBR2":
        result = calculate_nbr2(data["swir16"], data["swir22"])

    return result