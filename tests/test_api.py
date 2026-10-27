"""Tests for the EO processing API."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import eo.api as api
from eo.api import run_processing


VALID_AOI = {
    "type": "Polygon",
    "coordinates": [[
        [36.0, 2.0],
        [36.1, 2.0],
        [36.1, 2.1],
        [36.0, 2.1],
        [36.0, 2.0],
    ]],
}


@pytest.fixture
def valid_request():
    """A valid request for API tests."""
    return {
        "aoi": VALID_AOI,
        "start_date": "2026-02-01",
        "end_date": "2026-02-28",
        "products": ["NDVI"],
    }


@pytest.fixture(autouse=True)
def mock_shared_stac_search(monkeypatch):
    """Prevent tests from accessing the real Sentinel-2 catalog."""

    fake_item = SimpleNamespace(
        id="fake-sentinel-scene",
        properties={"eo:cloud_cover": 5},
    )
    fake_items = [fake_item]

    monkeypatch.setattr(
        api,
        "configure_s3_access",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        api,
        "connect_to_catalog",
        lambda: object(),
    )
    monkeypatch.setattr(
        api,
        "search_sentinel2",
        lambda *args, **kwargs: fake_items,
    )
    monkeypatch.setattr(
        api,
        "select_best_scene",
        lambda items: items[0],
    )


def fake_pipeline_result(kwargs):
    """Return a predictable, JSON-friendly pipeline result."""
    product = kwargs["process"]

    return {
        "status": "completed",
        "run_id": f"run-{product.lower()}",
        "process": product,
        "scene_count": 1,
        "product_path": f"/tmp/{product.lower()}.tif",
        "metadata_path": f"/tmp/{product.lower()}_metadata.json",
    }


def test_invalid_date_is_rejected(valid_request):
    """An invalid date should return a validation error."""
    request = {
        **valid_request,
        "start_date": "not-a-date",
    }

    result = run_processing(**request)

    assert result["status"] == "failed"
    assert result["errors"]
    assert result["errors"][0]["code"] == "INVALID_REQUEST"


def test_invalid_cloud_cover_is_rejected(valid_request):
    """Cloud cover must be within 0 to 100."""
    request = {
        **valid_request,
        "max_cloud_cover": 150,
    }

    result = run_processing(**request)

    assert result["status"] == "failed"
    assert result["errors"]
    assert result["errors"][0]["code"] == "INVALID_REQUEST"


def test_multiple_products_each_call_pipeline(
    valid_request, monkeypatch
):
    """Each requested product gets its own pipeline execution."""
    calls = []

    def fake_pipeline(**kwargs):
        calls.append(kwargs["process"])
        return fake_pipeline_result(kwargs)

    monkeypatch.setattr(api, "run_eo_pipeline", fake_pipeline)

    request = {
        **valid_request,
        "products": ["NDVI", "NDWI", "RGB"],
    }

    result = run_processing(**request)

    assert result["status"] == "completed"
    assert calls == ["NDVI", "NDWI", "RGB"]


def test_multiple_products_search_stac_once(
    valid_request, monkeypatch
):
    """Three products should share one Sentinel-2 search."""
    search_calls = []
    pipeline_calls = []

    fake_items = [object()]
    fake_reference_item = fake_items[0]

    def fake_search(*args, **kwargs):
        search_calls.append((args, kwargs))
        return fake_items

    def fake_select_best_scene(items):
        assert items is fake_items
        return fake_reference_item

    def fake_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        return fake_pipeline_result(kwargs)

    monkeypatch.setattr(api, "search_sentinel2", fake_search)
    monkeypatch.setattr(
        api, "select_best_scene", fake_select_best_scene
    )
    monkeypatch.setattr(api, "run_eo_pipeline", fake_pipeline)

    request = {
        **valid_request,
        "products": ["NDVI", "NDWI", "RGB"],
    }

    result = run_processing(**request)

    assert result["status"] == "completed"

    # One search supplies scenes for all three products.
    assert len(search_calls) == 1
    assert len(pipeline_calls) == 3

    for call in pipeline_calls:
        assert call["shared_items"] is fake_items
        assert call["shared_reference_item"] is fake_reference_item


def test_pipeline_failure_is_reported(valid_request, monkeypatch):
    """A product failure should be returned as an API error."""

    def failing_pipeline(**kwargs):
        raise RuntimeError("Test processing failure")

    monkeypatch.setattr(
        api, "run_eo_pipeline", failing_pipeline
    )

    result = run_processing(**valid_request)

    assert result["status"] == "failed"
    assert result["errors"]
    assert result["errors"][0]["code"] == "PROCESSING_FAILED"


def test_multiple_products_allow_partial_success(
    valid_request, monkeypatch
):
    """One failed product should not prevent another from completing."""

    def partly_failing_pipeline(**kwargs):
        if kwargs["process"] == "NDWI":
            raise RuntimeError("Test NDWI failure")
        return fake_pipeline_result(kwargs)

    monkeypatch.setattr(
        api, "run_eo_pipeline", partly_failing_pipeline
    )

    request = {
        **valid_request,
        "products": ["NDVI", "NDWI"],
    }

    result = run_processing(**request)

    assert result["status"] == "partial_success"
    assert result["errors"]


def test_result_is_json_serializable(valid_request, monkeypatch):
    """The API result must be suitable for a JSON response."""

    monkeypatch.setattr(
        api,
        "run_eo_pipeline",
        lambda **kwargs: fake_pipeline_result(kwargs),
    )

    result = run_processing(**valid_request)

    encoded = json.dumps(result)
    decoded = json.loads(encoded)

    assert decoded["status"] == "completed"


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        (
            "No Sentinel-2 scenes were found.",
            "NO_SCENES_FOUND",
        ),
        (
            "1 tile(s) failed. Completed tiles are kept in /tmp/test-run",
            "TILE_PROCESSING_FAILED",
        ),
        (
            "gdalbuildvrt and gdal_translate are required",
            "GDAL_TOOLS_MISSING",
        ),
        (
            "Unexpected test failure",
            "PROCESSING_FAILED",
        ),
    ],
)
def test_processing_errors_are_classified(
    valid_request, monkeypatch, message, expected_code, tmp_path
):
    """Known processing failures should receive the correct error code."""

    expected_run_dir = None
    expected_manifest = None

    if expected_code == "TILE_PROCESSING_FAILED":
        expected_run_dir = tmp_path / "test-run"
        expected_manifest = expected_run_dir / "tile_manifest.json"
        message = (
            "1 tile(s) failed. Completed tiles are kept in "
            f"{expected_run_dir}"
        )

    def failing_pipeline(**kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(
        api, "run_eo_pipeline", failing_pipeline
    )

    result = run_processing(**valid_request)

    assert result["status"] == "failed"
    assert result["errors"]
    assert result["errors"][0]["code"] == expected_code

    if expected_code == "TILE_PROCESSING_FAILED":
        assert result["errors"][0]["resumable"] is True
        assert Path(result["errors"][0]["run_dir"]) == expected_run_dir
        assert (
            Path(result["errors"][0]["manifest_path"])
            == expected_manifest
        )


def test_multi_product_request_creates_labelled_folders_and_metadata(
    valid_request, monkeypatch, tmp_path
):
    """A request groups products under one dated request folder."""
    pipeline_calls = []

    def fake_pipeline(**kwargs):
        product_dir = kwargs["output_dir"]
        product_dir.mkdir(parents=True, exist_ok=True)
        pipeline_calls.append(kwargs)

        return {
            "status": "completed",
            "run_id": f"run-{kwargs['process'].lower()}",
            "process": kwargs["process"],
            "scene_count": 1,
            "product_path": str(
                product_dir / f"{kwargs['process'].lower()}.tif"
            ),
            "metadata_path": str(product_dir / "metadata.json"),
        }

    monkeypatch.setattr(api, "run_eo_pipeline", fake_pipeline)

    request = {
        **valid_request,
        "products": ["NDVI", "RGB"],
        "output_dir": tmp_path,
    }

    result = run_processing(**request)

    assert result["status"] == "completed"

    request_dir = tmp_path / result["request_output_dir"].split("/")[-1]

    assert request_dir.is_dir()
    assert request_dir.name.startswith("20260201_20260228_request-")
    assert len(request_dir.name.rsplit("request-", 1)[1]) == 8

    # Both products use the same request folder, with their own subfolders.
    assert (request_dir / "NDVI").is_dir()
    assert (request_dir / "RGB").is_dir()
    assert len(pipeline_calls) == 2

    for call in pipeline_calls:
        assert call["create_run_directory"] is False
        assert call["output_dir"] == request_dir / call["process"]

    # Request metadata exists and records the product results.
    metadata_path = request_dir / "request_metadata.json"
    assert metadata_path.is_file()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["request_id"] == result["request_id"]
    assert metadata["requested_products"] == ["NDVI", "RGB"]
    assert metadata["status"] == "completed"
    assert set(metadata["products"]) == {"NDVI", "RGB"}

    # The API response exposes the request folder and metadata path.
    assert result["request_output_dir"] == str(request_dir)
    assert result["request_metadata_path"] == str(metadata_path)
