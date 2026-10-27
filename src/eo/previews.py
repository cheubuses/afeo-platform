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
