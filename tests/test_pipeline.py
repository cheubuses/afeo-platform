
from datetime import date
from types import SimpleNamespace

import eo.pipeline as pipeline


def test_shared_scenes_skip_catalog_search(monkeypatch, tmp_path):
    """Shared scenes should bypass STAC search."""

    fake_item = SimpleNamespace(
        id="fake-sentinel-scene",
        properties={
            "datetime": "2026-02-15T10:00:00Z",
            "eo:cloud_cover": 5,
        },
    )
    fake_items = [fake_item]
    tiled_calls = []

    def unexpected_search(*args, **kwargs):
        raise AssertionError(
            "STAC search should not run when shared scenes are supplied"
        )

    def fake_tiled_processor(**kwargs):
        tiled_calls.append(kwargs)
        return {
            "output_file": tmp_path / "ndvi.tif",
            "metadata_file": tmp_path / "metadata.json",
            "manifest_file": tmp_path / "manifest.json",
            "valid_count_file": None,
            "tile_count": 1,
            "elapsed_seconds": 0.1,
        }

    monkeypatch.setattr(pipeline, "configure_s3_access", unexpected_search)
    monkeypatch.setattr(pipeline, "connect_to_catalog", unexpected_search)
    monkeypatch.setattr(pipeline, "search_sentinel2", unexpected_search)
    monkeypatch.setattr(
        pipeline, "process_tiled_aoi", fake_tiled_processor
    )

    result = pipeline.run_eo_pipeline(
        aoi_geometry={
            "type": "Polygon",
            "coordinates": [[
                [36.0, 2.0],
                [36.1, 2.0],
                [36.1, 2.1],
                [36.0, 2.1],
                [36.0, 2.0],
            ]],
        },
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 28),
        process="NDVI",
        output_dir=tmp_path,
        shared_items=fake_items,
        shared_reference_item=fake_item,
    )

    assert result["status"] == "completed"
    assert result["scene_count"] == 1
    assert result["reference_scene"] == "fake-sentinel-scene"

    assert len(tiled_calls) == 1
    assert tiled_calls[0]["items"] is fake_items
    assert tiled_calls[0]["reference_item"] is fake_item
