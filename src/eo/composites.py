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