from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from eo.previews import create_cog_preview


def test_create_cog_preview_generates_rgba_png(tmp_path):
    cog = tmp_path / "rgb.tif"
    preview = tmp_path / "rgb_preview.png"

    data = np.arange(
        3 * 80 * 100, dtype=np.float32
    ).reshape(3, 80, 100)

    with rasterio.open(
        cog,
        "w",
        driver="GTiff",
        width=100,
        height=80,
        count=3,
        dtype="float32",
        crs="EPSG:32636",
        transform=from_origin(500000, 1000000, 10, 10),
        nodata=-9999,
    ) as dst:
        dst.write(data)

    result = create_cog_preview(
        cog, preview, "RGB", max_dimension=50
    )

    assert result == preview
    assert preview.is_file()

    with Image.open(preview) as image:
        assert image.size == (50, 40)
        assert image.mode == "RGBA"


def test_create_cog_preview_rejects_unsupported_product(tmp_path):
    cog = tmp_path / "ndvi.tif"
    cog.touch()

    try:
        create_cog_preview(cog, tmp_path / "ndvi.png", "NDVI")
    except ValueError as exc:
        assert "not supported" in str(exc)
    else:
        raise AssertionError("Expected unsupported product to fail.")



def test_create_ndwi_preview_generates_legend_png(tmp_path):
    cog = tmp_path / "ndwi.tif"
    preview = tmp_path / "ndwi_preview.png"
    data = np.linspace(-1.0, 1.0, 80 * 100, dtype=np.float32).reshape(80, 100)

    with rasterio.open(
        cog,
        "w",
        driver="GTiff",
        width=100,
        height=80,
        count=1,
        dtype="float32",
        crs="EPSG:32636",
        transform=from_origin(500000, 1000000, 10, 10),
        nodata=-9999,
    ) as dst:
        dst.write(data, 1)

    from eo.previews import create_ndwi_preview

    result = create_ndwi_preview(cog, preview, max_dimension=50)
    assert result == preview
    assert preview.is_file()
    with Image.open(preview) as image:
        assert image.format == "PNG"
        assert image.width > 0 and image.height > 0
