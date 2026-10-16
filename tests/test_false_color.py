import sys
from pathlib import Path

from affine import Affine
import numpy as np
import rasterio
import rioxarray  # Registers the .rio accessor.
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eo.composites import save_color_preview
from eo.export import export_cog
from eo.processor import process_data


def test_false_color_processing_and_cog_export(tmp_path):
    data = xr.Dataset({
        "nir": (("y", "x"), np.full((4, 5), 0.8, dtype=np.float32)),
        "red": (("y", "x"), np.full((4, 5), 0.3, dtype=np.float32)),
        "green": (("y", "x"), np.full((4, 5), 0.2, dtype=np.float32)),
        "blue": (("y", "x"), np.full((4, 5), 0.1, dtype=np.float32)),
    })

    result = process_data(data, "false_color")

    assert result.band.values.tolist() == ["nir", "red", "green"]
    np.testing.assert_allclose(result.values[:, 0, 0], [0.8, 0.3, 0.2])

    preview_path = tmp_path / "false_color_preview.png"
    save_color_preview(
        result,
        preview_path,
        band_order=("nir", "red", "green"),
        title="Sentinel-2 false-colour infrared composite",
    )
    assert preview_path.is_file()
    assert preview_path.stat().st_size > 0

    result = result.rio.write_crs("EPSG:32643")
    result = result.rio.write_transform(
        Affine(10, 0, 500000, 0, -10, 1000000)
    )

    output_path = tmp_path / "false_color.tif"
    export_cog(result, output_path)

    with rasterio.open(output_path) as output:
        assert output.count == 3
        assert output.crs.to_string() == "EPSG:32643"
        np.testing.assert_allclose(
            output.read()[:, 0, 0],
            [0.8, 0.3, 0.2],
        )
