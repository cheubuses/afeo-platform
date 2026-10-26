import numpy as np
import rioxarray
import xarray as xr

from affine import Affine
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform as transform_geometry

from eo.tiling import build_single_aoi_window
from eo.tiled_pipeline import _mask_array_to_aoi


def test_small_aoi_fits_one_pixel_aligned_window():
    aoi = box(35.98, 1.98, 36.02, 2.02)

    windows = build_single_aoi_window(
        aoi,
        target_crs="EPSG:32636",
        pixel_size_m=20,
        max_window_size_m=10000,
    )

    assert len(windows) == 1

    xmin, ymin, xmax, ymax = windows[0]["bounds"]
    assert xmax > xmin
    assert ymax > ymin
    assert (xmax - xmin) <= 10000
    assert (ymax - ymin) <= 10000

    for coordinate in (xmin, ymin, xmax, ymax):
        assert coordinate % 20 == 0

    assert windows[0]["aoi_geometry_wgs84"].equals(aoi)


def test_large_aoi_does_not_fit_single_window():
    # Approximately 0.2 degrees wide: exceeds a 10 km window.
    aoi = box(35.8, 1.9, 36.0, 2.1)

    windows = build_single_aoi_window(
        aoi,
        target_crs="EPSG:32636",
        pixel_size_m=20,
        max_window_size_m=10000,
    )

    assert windows == []


def test_aoi_mask_sets_outside_pixels_to_nan():
    # Build a 10 x 10 raster with 100 m pixels in UTM coordinates.
    crs = "EPSG:32636"
    transform = Affine(100, 0, 800000, 0, -100, 250000)

    # Create an AOI inside the raster, using its projected coordinates.
    projected_aoi = box(800200, 249300, 800700, 249800)
    to_wgs84 = Transformer.from_crs(
        crs, "EPSG:4326", always_xy=True
    ).transform
    aoi_wgs84 = transform_geometry(to_wgs84, projected_aoi)

    values = np.ones((10, 10), dtype="float32")
    raster = xr.DataArray(
        values,
        dims=("y", "x"),
        coords={
            "y": 250000 - 50 - np.arange(10) * 100,
            "x": 800000 + 50 + np.arange(10) * 100,
        },
    )
    raster = raster.rio.write_crs(crs)
    raster = raster.rio.write_transform(transform)
    raster = raster.rio.write_nodata(np.nan)

    before = int(np.isfinite(raster.values).sum())
    masked = _mask_array_to_aoi(raster, aoi_wgs84)
    after = int(np.isfinite(masked.values).sum())

    assert before == 100
    assert 0 < after < before
    assert np.isnan(masked.values).any()
    assert np.isfinite(masked.values).any()


def _run_until_mode_selected(monkeypatch, tmp_path, aoi, mode="auto"):
    """Stop after mode selection, before any satellite processing."""
    import eo.tiled_pipeline as tiled

    class StopAfterModeSelection(Exception):
        pass

    captured = {}

    monkeypatch.setattr(
        tiled.shutil,
        "which",
        lambda name: f"/mock/bin/{name}",
    )
    monkeypatch.setattr(
        tiled,
        "choose_target_crs",
        lambda items, geometry: "EPSG:32636",
    )

    def stop_at_run_key(**kwargs):
        captured["mode"] = kwargs["processing_mode"]
        raise StopAfterModeSelection()

    monkeypatch.setattr(tiled, "_run_key", stop_at_run_key)

    if mode == "auto" and aoi.bounds[2] - aoi.bounds[0] > 0.1:
        monkeypatch.setattr(
            tiled,
            "build_aoi_tiles",
            lambda *args, **kwargs: [
                {
                    "tile_id": "test_tile",
                    "bounds": (800000, 200000, 805000, 205000),
                    "aoi_geometry_wgs84": aoi,
                }
            ],
        )

    try:
        tiled.process_tiled_aoi(
            items=[object()],
            aoi_shape=aoi,
            process="NDVI",
            composite_method="median",
            start_date="2026-03-01",
            end_date="2026-03-31",
            cloud_cover_threshold=20,
            reference_item=None,
            output_dir=tmp_path,
            project_root=tmp_path,
            input_parameters={},
            processing_mode=mode,
        )
    except StopAfterModeSelection:
        pass
    else:
        raise AssertionError("Expected the test to stop after mode selection")

    return captured["mode"]


def test_auto_mode_selects_single_window_for_small_aoi(
    monkeypatch, tmp_path
):
    from shapely.geometry import box

    aoi = box(35.98, 1.98, 36.02, 2.02)

    mode = _run_until_mode_selected(
        monkeypatch, tmp_path, aoi, mode="auto"
    )

    assert mode == "single_window"


def test_auto_mode_selects_spatial_tiles_for_large_aoi(
    monkeypatch, tmp_path
):
    from shapely.geometry import box

    aoi = box(35.8, 1.9, 36.0, 2.1)

    mode = _run_until_mode_selected(
        monkeypatch, tmp_path, aoi, mode="auto"
    )

    assert mode == "spatially_tiled"


def test_single_window_mode_rejects_large_aoi(monkeypatch, tmp_path):
    import pytest
    import eo.tiled_pipeline as tiled
    from shapely.geometry import box

    monkeypatch.setattr(
        tiled.shutil, "which", lambda name: f"/mock/bin/{name}"
    )
    monkeypatch.setattr(
        tiled,
        "choose_target_crs",
        lambda items, geometry: "EPSG:32636",
    )

    aoi = box(35.8, 1.9, 36.0, 2.1)

    with pytest.raises(ValueError, match="AOI exceeds max_window_size_m"):
        tiled.process_tiled_aoi(
            items=[object()],
            aoi_shape=aoi,
            process="NDVI",
            composite_method="median",
            start_date="2026-03-01",
            end_date="2026-03-31",
            cloud_cover_threshold=20,
            reference_item=None,
            output_dir=tmp_path,
            project_root=tmp_path,
            input_parameters={},
            processing_mode="single_window",
        )
