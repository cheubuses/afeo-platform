
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


def save_rgb_preview(data, output_path, stretch_limits=None):
    """Save an RGB PNG preview using optional shared display limits."""
    import numpy as np
    import matplotlib.pyplot as plt

    rgb = create_rgb(data) if isinstance(data, xr.Dataset) else data

    if "time" in rgb.dims:
        raise ValueError("Create the temporal composite before the RGB preview.")

    if "band" not in rgb.dims or "band" not in rgb.coords:
        raise ValueError("RGB data must have a labelled band dimension.")

    expected = ["red", "green", "blue"]
    labels = [str(value) for value in rgb.band.values]

    if not all(name in labels for name in expected):
        raise ValueError(f"Expected RGB bands {expected}; found {labels}.")

    rgb = rgb.sel(band=expected).compute()
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
    ax.set_title("Sentinel-2 RGB composite")
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

