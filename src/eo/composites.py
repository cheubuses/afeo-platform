
import xarray as xr

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


def create_false_color(data):
    """
    Create a false-colour infrared composite from Sentinel-2 data.

    Display channels are NIR (red), red (green), and green (blue).
    """

    bands = ["nir", "red", "green"]
    false_color = data[bands].to_array(dim="band")
    false_color = false_color.assign_coords(band=bands)
    false_color.attrs.update({
        "long_name": "Sentinel-2 false-colour infrared composite",
        "display_channels": "NIR, red, green",
    })
    return false_color




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


def save_rgb_preview(
    data,
    output_path,
    stretch_limits=None,
    max_preview_dimension=1600,
):
    """Save an RGB PNG preview using optional shared display limits."""
    return save_color_preview(
        data,
        output_path,
        band_order=("red", "green", "blue"),
        title="Sentinel-2 RGB composite",
        stretch_limits=stretch_limits,
        max_preview_dimension=max_preview_dimension,
    )


def save_color_preview(
    data,
    output_path,
    *,
    band_order,
    title,
    stretch_limits=None,
    max_preview_dimension=1600,
):
    """Save a three-channel colour composite preview."""
    import numpy as np
    import matplotlib.pyplot as plt

    if isinstance(data, xr.Dataset):
        rgb = data[list(band_order)].to_array(dim="band")
        rgb = rgb.assign_coords(band=list(band_order))
    else:
        rgb = data

    if "time" in rgb.dims:
        raise ValueError(
            "Create the temporal composite before the colour preview."
        )

    if "band" not in rgb.dims or "band" not in rgb.coords:
        raise ValueError(
            "Colour-composite data must have a labelled band dimension."
        )

    expected = list(band_order)
    labels = [str(value) for value in rgb.band.values]

    if len(expected) != 3 or not all(name in labels for name in expected):
        raise ValueError(
            f"Expected colour bands {expected}; found {labels}."
        )

    # ------------------------------------------------------------
    # Reduce resolution for preview generation only.
    # The original composite and exported COG remain unchanged.
    # ------------------------------------------------------------

    height = int(rgb.sizes["y"])
    width = int(rgb.sizes["x"])

    downsample_factor = max(
        1,
        int(
            np.ceil(
                max(height, width) / max_preview_dimension
            )
        ),
    )

    rgb = rgb.sel(band=expected)

    if downsample_factor > 1:
        print(
            f"Downsampling RGB preview by "
            f"{downsample_factor}x for display."
        )

        rgb = rgb.coarsen(
            y=downsample_factor,
            x=downsample_factor,
            boundary="trim",
        ).mean(skipna=True)

    # Compute the reduced preview, not the full-resolution image.
    rgb = rgb.compute()

    values = np.asarray(rgb.values, dtype=np.float32)

    if values.ndim != 3 or values.shape[0] != 3:
        raise ValueError(f"Expected RGB data shaped (3, y, x); got {values.shape}.")

    valid = np.isfinite(values).all(axis=0)

    if not valid.any():
        raise ValueError("No valid RGB pixels are available.")

    if stretch_limits is None:
        limits = []
        for index in range(3):
            low, high = np.percentile(values[index][valid], [2, 98])
            if high <= low:
                high = low + 1e-6
            limits.append((float(low), float(high)))
    else:
        if len(stretch_limits) != 3:
            raise ValueError("Provide three (low, high) stretch pairs.")
        limits = [(float(low), float(high)) for low, high in stretch_limits]

    display = np.zeros(
        (values.shape[1], values.shape[2], 3),
        dtype=np.float32,
    )

    for index, (low, high) in enumerate(limits):
        display[:, :, index] = np.clip(
            (values[index] - low) / (high - low),
            0,
            1,
        )

    alpha = valid.astype(np.float32)

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(display, alpha=alpha)
    ax.set_title(title)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(
        output_path,
        dpi=150,
        bbox_inches="tight",
        pad_inches=0.05,
    )
    plt.close(fig)

    return limits
