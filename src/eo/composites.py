def create_rgb(data):
    """
    Create an RGB composite from Sentinel-2 data.

    Uses:
    Red   = Sentinel-2 red band
    Green = Sentinel-2 green band
    Blue  = Sentinel-2 blue band
    """

    rgb = data[
        [
            "red",
            "green",
            "blue"
        ]
    ]

    rgb_stack = rgb.to_array(
        dim="band"
    )

    rgb_stack = rgb_stack.assign_coords(
        band=[
            "red",
            "green",
            "blue"
        ]
    )

    return rgb_stack




def create_temporal_composite(data, method="median"):
    """
    Create a monthly temporal composite using
    either median or mean across the time dimension.
    """

    method = method.strip().lower()

    if "time" not in data.dims:
        raise ValueError(
            "The input dataset must contain a time dimension."
        )

    if method == "median":
        composite = data.median(
            dim="time",
            skipna=True
        )

    elif method == "mean":
        composite = data.mean(
            dim="time",
            skipna=True
        )

    else:
        raise ValueError(
            "Invalid composite method. "
            "Choose 'median' or 'mean'."
        )

    return composite
