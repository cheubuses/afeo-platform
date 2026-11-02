"""Create lightweight PNG previews from exported EO COGs."""

from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling


PREVIEW_PRODUCTS = {
    "RGB": ("red", "green", "blue"),
    "FALSE_COLOR": ("nir", "red", "green"),
}


def create_cog_preview(
    cog_path,
    preview_path,
    product,
    max_dimension=1200,
):
    """Create an RGB/RGBA PNG preview from the first three COG bands."""
    product = str(product).strip().upper()

    if product not in PREVIEW_PRODUCTS:
        raise ValueError(
            f"Previews are not supported for product {product!r}."
        )

    cog_path = Path(cog_path)
    preview_path = Path(preview_path)

    if not cog_path.is_file():
        raise FileNotFoundError(f"COG does not exist: {cog_path}")

    if max_dimension < 1:
        raise ValueError("max_dimension must be positive.")

    preview_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(cog_path) as src:
        if src.count < 3:
            raise ValueError(
                f"{product} preview requires three bands; "
                f"the COG contains {src.count}."
            )

        scale = min(1.0, max_dimension / max(src.width, src.height))
        width = max(1, round(src.width * scale))
        height = max(1, round(src.height * scale))

        values = src.read(
            indexes=[1, 2, 3],
            out_shape=(3, height, width),
            resampling=Resampling.average,
            masked=True,
        )

        data = np.asarray(values.data, dtype=np.float32)
        valid = (
            ~np.ma.getmaskarray(values).any(axis=0)
            & np.isfinite(data).all(axis=0)
        )

    if not valid.any():
        raise ValueError(f"{product} COG has no valid pixels for preview.")

    display = np.zeros((height, width, 4), dtype=np.uint8)

    for channel in range(3):
        samples = data[channel][valid]
        low, high = np.percentile(samples, [2, 98])

        if not np.isfinite(low) or not np.isfinite(high):
            raise ValueError("Could not calculate preview stretch limits.")

        if high <= low:
            high = low + 1e-6

        stretched = np.clip(
            (data[channel] - low) / (high - low),
            0,
            1,
        )
        display[:, :, channel] = np.round(
            np.nan_to_num(stretched) * 255
        ).astype(np.uint8)

    display[:, :, 3] = valid.astype(np.uint8) * 255
    Image.fromarray(display, mode="RGBA").save(preview_path, format="PNG")

    return preview_path



def create_ndwi_preview(
    cog_path,
    preview_path,
    *,
    max_dimension=1200,
):
    """Create a display-only NDWI PNG with a fixed -1..1 water legend.

    The input COG is read-only and remains the scientific analysis product.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    cog_path = Path(cog_path)
    preview_path = Path(preview_path)

    if not cog_path.is_file():
        raise FileNotFoundError(f"COG does not exist: {cog_path}")
    if max_dimension < 1:
        raise ValueError("max_dimension must be positive.")

    preview_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(cog_path) as src:
        if src.count != 1:
            raise ValueError(
                f"NDWI preview requires one band; COG has {src.count}."
            )
        scale = min(1.0, max_dimension / max(src.width, src.height))
        width = max(1, round(src.width * scale))
        height = max(1, round(src.height * scale))
        band = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.average,
            masked=True,
        )
        values = np.asarray(band.data, dtype=np.float32)
        valid = ~np.ma.getmaskarray(band) & np.isfinite(values)

    if not valid.any():
        raise ValueError("NDWI COG has no valid pixels for preview.")

    # Fixed limits make previews comparable across AOIs and dates.
    palette = ["#8c510a", "#d8b365", "#f6e8c3",
               "#c7eae5", "#5ab4ac", "#01665e"]
    cmap = LinearSegmentedColormap.from_list("afrosat_ndwi", palette, N=256)
    display = np.ma.array(np.clip(values, -1.0, 1.0), mask=~valid)

    fig, ax = plt.subplots(figsize=(8, 6), constrained_layout=True)
    image = ax.imshow(display, cmap=cmap, norm=Normalize(-1.0, 1.0))
    ax.set_title("NDWI — water index")
    ax.axis("off")
    colorbar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.03)
    colorbar.set_label("NDWI (unitless)")
    colorbar.set_ticks([-1.0, -0.5, 0.0, 0.5, 1.0])
    colorbar.ax.set_yticklabels(["-1 Dry", "-0.5", "0", "0.5", "+1 Water"])
    fig.savefig(preview_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return preview_path


def create_ndvi_preview(
    cog_path,
    preview_path,
    *,
    max_dimension=1200,
):
    """Create a display-only NDVI PNG with a fixed -1..1 legend.

    The scientific NDVI COG is read-only and remains unchanged.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    cog_path = Path(cog_path)
    preview_path = Path(preview_path)

    if not cog_path.is_file():
        raise FileNotFoundError(f"COG does not exist: {cog_path}")
    if max_dimension < 1:
        raise ValueError("max_dimension must be positive.")

    preview_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(cog_path) as src:
        if src.count != 1:
            raise ValueError(
                f"NDVI preview requires one band; COG has {src.count}."
            )

        scale = min(
            1.0,
            max_dimension / max(src.width, src.height),
        )
        width = max(1, round(src.width * scale))
        height = max(1, round(src.height * scale))

        band = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.average,
            masked=True,
        )
        values = np.asarray(band.data, dtype=np.float32)
        valid = ~np.ma.getmaskarray(band) & np.isfinite(values)

    if not valid.any():
        raise ValueError("NDVI COG has no valid pixels for preview.")

    # Brown = low/negative NDVI; green = high positive NDVI.
    palette = [
        "#8c510a",
        "#d8b365",
        "#f6e8c3",
        "#a6d96a",
        "#1a9850",
        "#006837",
    ]
    cmap = LinearSegmentedColormap.from_list(
        "afrosat_ndvi", palette, N=256
    )
    cmap.set_bad((0, 0, 0, 0))

    display = np.ma.array(
        np.clip(values, -1.0, 1.0),
        mask=~valid,
    )

    fig, ax = plt.subplots(figsize=(8, 6), constrained_layout=True)
    image = ax.imshow(
        display,
        cmap=cmap,
        norm=Normalize(-1.0, 1.0),
        interpolation="nearest",
    )
    ax.set_title("NDVI — vegetation index")
    ax.axis("off")

    colorbar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.03)
    colorbar.set_label("NDVI (unitless)")
    colorbar.set_ticks([-1.0, -0.5, 0.0, 0.5, 1.0])

    fig.savefig(
        preview_path,
        dpi=130,
        bbox_inches="tight",
        transparent=True,
    )
    plt.close(fig)

    return preview_path


def create_ndmi_preview(
    cog_path,
    preview_path,
    *,
    max_dimension=1200,
):
    """Create a display-only NDMI PNG with a fixed -1..1 legend.

    The scientific NDMI COG is read-only and remains unchanged.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    cog_path = Path(cog_path)
    preview_path = Path(preview_path)

    if not cog_path.is_file():
        raise FileNotFoundError(f"COG does not exist: {cog_path}")

    if max_dimension < 1:
        raise ValueError("max_dimension must be positive.")

    preview_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(cog_path) as src:
        if src.count != 1:
            raise ValueError(
                f"NDMI preview requires one band; COG has {src.count}."
            )

        scale = min(
            1.0,
            max_dimension / max(src.width, src.height),
        )
        width = max(1, round(src.width * scale))
        height = max(1, round(src.height * scale))

        band = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.average,
            masked=True,
        )

        values = np.asarray(band.data, dtype=np.float32)
        valid = ~np.ma.getmaskarray(band) & np.isfinite(values)

    if not valid.any():
        raise ValueError("NDMI COG has no valid pixels for preview.")

    # Lower values indicate less vegetation moisture;
    # higher values indicate greater vegetation moisture.
    palette = [
        "#8c510a",
        "#d8b365",
        "#f6e8c3",
        "#a6d96a",
        "#5ab4ac",
        "#01665e",
    ]

    cmap = LinearSegmentedColormap.from_list(
        "afrosat_ndmi",
        palette,
        N=256,
    )
    cmap.set_bad((0, 0, 0, 0))

    display = np.ma.array(
        np.clip(values, -1.0, 1.0),
        mask=~valid,
    )

    fig, ax = plt.subplots(figsize=(8, 6), constrained_layout=True)

    image = ax.imshow(
        display,
        cmap=cmap,
        norm=Normalize(-1.0, 1.0),
        interpolation="nearest",
    )

    ax.set_title("NDMI — vegetation moisture index")
    ax.axis("off")

    colorbar = fig.colorbar(
        image,
        ax=ax,
        fraction=0.045,
        pad=0.03,
    )
    colorbar.set_label("NDMI (unitless)")
    colorbar.set_ticks([-1.0, -0.5, 0.0, 0.5, 1.0])

    fig.savefig(
        preview_path,
        dpi=130,
        bbox_inches="tight",
    )
    plt.close(fig)

    return preview_path


def create_burn_index_preview(
    cog_path,
    preview_path,
    product,
    *,
    max_dimension=1200,
):
    """Create a display-only NBR or NBR2 PNG with a fixed -1..1 legend.

    The scientific COG is read-only and remains unchanged.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    product = str(product).strip().upper()

    if product not in {"NBR", "NBR2"}:
        raise ValueError(
            f"Burn-index preview does not support {product!r}."
        )

    cog_path = Path(cog_path)
    preview_path = Path(preview_path)

    if not cog_path.is_file():
        raise FileNotFoundError(f"COG does not exist: {cog_path}")

    if max_dimension < 1:
        raise ValueError("max_dimension must be positive.")

    preview_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(cog_path) as src:
        if src.count != 1:
            raise ValueError(
                f"{product} preview requires one band; "
                f"COG has {src.count}."
            )

        scale = min(
            1.0,
            max_dimension / max(src.width, src.height),
        )
        width = max(1, round(src.width * scale))
        height = max(1, round(src.height * scale))

        band = src.read(
            1,
            out_shape=(height, width),
            resampling=Resampling.average,
            masked=True,
        )

        values = np.asarray(band.data, dtype=np.float32)
        valid = ~np.ma.getmaskarray(band) & np.isfinite(values)

    if not valid.any():
        raise ValueError(
            f"{product} COG has no valid pixels for preview."
        )

    # Low values are shown in brown/red tones; high values in green tones.
    palette = [
        "#a50026",
        "#d73027",
        "#fdae61",
        "#fee08b",
        "#d9ef8b",
        "#66bd63",
        "#1a9850",
    ]

    cmap = LinearSegmentedColormap.from_list(
        f"afrosat_{product.lower()}",
        palette,
        N=256,
    )
    cmap.set_bad((0, 0, 0, 0))

    display = np.ma.array(
        np.clip(values, -1.0, 1.0),
        mask=~valid,
    )

    fig, ax = plt.subplots(figsize=(8, 6), constrained_layout=True)

    image = ax.imshow(
        display,
        cmap=cmap,
        norm=Normalize(-1.0, 1.0),
        interpolation="nearest",
    )

    title = (
        "NBR — normalized burn ratio"
        if product == "NBR"
        else "NBR2 — normalized burn ratio 2"
    )
    ax.set_title(title)
    ax.axis("off")

    colorbar = fig.colorbar(
        image,
        ax=ax,
        fraction=0.045,
        pad=0.03,
    )
    colorbar.set_label(f"{product} (unitless)")
    colorbar.set_ticks([-1.0, -0.5, 0.0, 0.5, 1.0])

    fig.savefig(
        preview_path,
        dpi=130,
        bbox_inches="tight",
        transparent=True,
    )
    plt.close(fig)

    return preview_path


def create_nbr_preview(cog_path, preview_path, *, max_dimension=1200):
    """Create a display-only NBR preview."""
    return create_burn_index_preview(
        cog_path,
        preview_path,
        "NBR",
        max_dimension=max_dimension,
    )


def create_nbr2_preview(cog_path, preview_path, *, max_dimension=1200):
    """Create a display-only NBR2 preview."""
    return create_burn_index_preview(
        cog_path,
        preview_path,
        "NBR2",
        max_dimension=max_dimension,
    )
