
from eo.indices import calculate_ndvi
from eo.composites import create_rgb


def process_data(data, process):
    """
    Run the requested Earth Observation process.
    """

    process = process.upper()

    if process == "RGB":
        result = create_rgb(data)

    elif process == "NDVI":
        red = data["red"]
        nir = data["nir"]

        result = calculate_ndvi(
            red,
            nir
        )

    else:
        raise ValueError(
            f"Unsupported process: {process}"
        )

    return result