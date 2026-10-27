import json
from pathlib import Path

from eo.metadata import _file_record


def test_file_record_uses_relative_posix_path(tmp_path):
    project_root = tmp_path / "project"
    output_dir = project_root / "outputs"
    output_dir.mkdir(parents=True)

    output_file = output_dir / "rgb_preview.png"
    output_file.write_bytes(b"preview-test")

    record = _file_record(output_file, project_root)

    assert record["filename"] == "rgb_preview.png"
    assert record["path"] == "outputs/rgb_preview.png"
    assert not Path(record["path"]).is_absolute()
    assert record["size_bytes"] == len(b"preview-test")
    assert len(record["sha256"]) == 64


def test_file_record_does_not_expose_external_absolute_path(tmp_path):
    project_root = tmp_path / "project"
    external_dir = tmp_path / "external"
    project_root.mkdir()
    external_dir.mkdir()

    output_file = external_dir / "result.tif"
    output_file.write_bytes(b"raster-test")

    record = _file_record(output_file, project_root)

    assert record["path"] == "result.tif"
    assert str(external_dir) not in json.dumps(record)
    assert not Path(record["path"]).is_absolute()


def test_create_metadata_saves_preview_reference_in_json(tmp_path):
    import hashlib
    from types import SimpleNamespace

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import box

    from eo.metadata import create_metadata, save_metadata

    project_root = tmp_path
    output_dir = project_root / "outputs"
    output_dir.mkdir(parents=True)

    cog_path = output_dir / "rgb.tif"
    preview_path = output_dir / "rgb_preview.png"
    metadata_path = output_dir / "metadata.json"

    # Create a small raster to represent the exported RGB COG.
    raster_data = np.arange(
        3 * 10 * 12, dtype=np.float32
    ).reshape(3, 10, 12)

    with rasterio.open(
        cog_path,
        "w",
        driver="GTiff",
        width=12,
        height=10,
        count=3,
        dtype="float32",
        crs="EPSG:32636",
        transform=from_origin(500000, 1000000, 10, 10),
        nodata=-9999,
    ) as dst:
        dst.write(raster_data)
        dst.set_band_description(1, "red")
        dst.set_band_description(2, "green")
        dst.set_band_description(3, "blue")

    preview_bytes = b"test-preview-png-content"
    preview_path.write_bytes(preview_bytes)

    # A minimal stand-in for a STAC scene; no network request is made.
    scene = SimpleNamespace(
        id="test-scene",
        collection_id="sentinel-2-c1-l2a",
        properties={
            "datetime": "2026-03-15T08:00:00Z",
            "eo:cloud_cover": 5.0,
            "platform": "sentinel-2b",
            "constellation": "sentinel-2",
            "processing:level": "L2A",
        },
    )

    metadata = create_metadata(
        output_path=cog_path,
        aoi_shape=box(35.0, 1.0, 35.1, 1.1),
        input_parameters={},
        start_date="2026-03-01",
        end_date="2026-03-31",
        cloud_cover_threshold=20,
        items=[scene],
        reference_item=scene,
        composite_method="median",
        process="RGB",
        mask_stats={},
        project_root=project_root,
        preview_paths={"rgb_preview": preview_path},
    )

    save_metadata(metadata, metadata_path)

    # Verify the saved JSON contains a portable preview reference.
    import json

    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    preview_record = saved["outputs"]["rgb_preview"]

    assert preview_record["filename"] == "rgb_preview.png"
    assert preview_record["path"] == "outputs/rgb_preview.png"
    assert preview_record["size_bytes"] == len(preview_bytes)
    assert preview_record["sha256"] == hashlib.sha256(
        preview_bytes
    ).hexdigest()
    assert "cog" in saved["outputs"]
    assert str(project_root) not in json.dumps(preview_record)
    
    
def test_manifest_paths_are_portable_and_internal_records_are_unchanged(
    tmp_path,
):
    from eo.tiled_pipeline import _write_manifest

    project_root = tmp_path / "project"
    run_dir = project_root / "outputs" / "tiles" / "ndwi_run"
    run_dir.mkdir(parents=True)

    manifest_path = run_dir / "tile_manifest.json"
    tile_path = run_dir / "ndwi_tile_1.tif"
    count_path = run_dir / "validcount_tile_1.tif"

    records = [
        {
            "tile_id": "tile_1",
            "status": "completed",
            "file": str(tile_path),
            "count_file": str(count_path),
        }
    ]

    _write_manifest(
        manifest_path,
        {"product": "NDWI"},
        records,
        "completed",
        project_root=project_root,
    )

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    saved_record = saved["tiles"][0]

    assert saved_record["file"] == (
        "outputs/tiles/ndwi_run/ndwi_tile_1.tif"
    )
    assert saved_record["count_file"] == (
        "outputs/tiles/ndwi_run/validcount_tile_1.tif"
    )

    assert not Path(saved_record["file"]).is_absolute()
    assert not Path(saved_record["count_file"]).is_absolute()

    # The records used by the processing code remain local absolute paths.
    assert records[0]["file"] == str(tile_path)
    assert records[0]["count_file"] == str(count_path)

