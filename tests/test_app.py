"""Tests for the FastAPI HTTP interface without an HTTP test client."""

import asyncio

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

import api.app as app_module


client_aoi = {
    "type": "Polygon",
    "coordinates": [[
        [36.0, 2.0],
        [36.1, 2.0],
        [36.1, 2.1],
        [36.0, 2.1],
        [36.0, 2.0],
    ]],
}

BASE_REQUEST = {
    "aoi": client_aoi,
    "start_date": "2026-03-01",
    "end_date": "2026-03-31",
}


def call_process(request):
    """Call the async endpoint directly using a validated request model."""
    model = app_module.EOProcessRequest(**request)
    return asyncio.run(app_module.process(model))


def install_direct_threadpool(monkeypatch):
    """Run mocked synchronous pipeline functions without a thread pool."""

    async def direct_call(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(
        app_module,
        "run_in_threadpool",
        direct_call,
    )


def test_health_endpoint():
    result = app_module.health()

    assert result["status"] == "ok"
    assert result["service"] == "afrosat-eo"


def test_products_endpoint_uses_pipeline_product_list():
    result = app_module.supported_products()

    assert result["products"] == list(app_module.OUTPUT_NAMES)


def test_single_product_request_uses_common_response_contract(monkeypatch):
    install_direct_threadpool(monkeypatch)
    captured = {}

    def fake_processing(**kwargs):
        captured.update(kwargs)
        return {
            "status": "completed",
            "requested_products": kwargs["products"],
            "completed_products": 1,
            "failed_products": 0,
            "products": {
                "NDWI": {
                    "status": "completed",
                    "process": "NDWI",
                    "product_path": "/tmp/ndwi.tif",
                }
            },
            "errors": [],
        }

    monkeypatch.setattr(app_module, "run_processing", fake_processing)

    result = call_process({
        **BASE_REQUEST,
        "process": "NDWI",
    })

    assert result["status"] == "completed"
    assert result["process"] == "NDWI"
    assert captured["products"] == ["NDWI"]
    assert set(result["products"]) == {"NDWI"}
    assert "frontend" in result["products"]["NDWI"]


def test_multi_product_request_uses_wrapper(monkeypatch):
    install_direct_threadpool(monkeypatch)
    captured = {}

    def fake_processing(**kwargs):
        captured.update(kwargs)
        return {
            "status": "completed",
            "requested_products": kwargs["products"],
            "completed_products": len(kwargs["products"]),
            "failed_products": 0,
            "products": {},
            "errors": [],
        }

    monkeypatch.setattr(
        app_module,
        "run_processing",
        fake_processing,
    )

    requested_products = list(app_module.OUTPUT_NAMES)

    result = call_process({
        **BASE_REQUEST,
        "products": requested_products,
        "write_valid_count": True,
        "keep_tiles": False,
    })

    assert result["status"] == "completed"
    assert result["requested_products"] == requested_products
    assert captured["products"] == requested_products
    assert captured["write_valid_count"] is True
    assert captured["keep_tiles"] is False


def test_invalid_date_range_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        call_process({
            **BASE_REQUEST,
            "start_date": "2026-04-01",
            "end_date": "2026-03-01",
        })

    assert exc_info.value.status_code == 422


def test_unknown_request_field_is_rejected():
    with pytest.raises(ValidationError):
        app_module.EOProcessRequest(
            **BASE_REQUEST,
            unexpected_field=True,
        )



def test_ndwi_frontend_links_include_display_legend(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    product_dir = outputs / "request" / "NDWI"
    product_dir.mkdir(parents=True)
    cog = product_dir / "ndwi.tif"
    preview = product_dir / "ndwi_preview.png"
    metadata = product_dir / "metadata.json"
    manifest = product_dir / "tile_manifest.json"
    for path in (cog, preview, metadata, manifest):
        path.write_bytes(b"test")

    monkeypatch.setattr(app_module, "OUTPUTS_DIR", outputs)
    result = app_module._add_frontend_links({
        "status": "completed",
        "products": {
            "NDWI": {
                "status": "completed",
                "process": "NDWI",
                "product_path": str(cog),
                "preview_path": str(preview),
                "metadata_path": str(metadata),
                "manifest_path": str(manifest),
            }
        },
        "request_metadata_path": str(product_dir / "request_metadata.json"),
    })
    frontend = result["products"]["NDWI"]["frontend"]
    assert frontend["product_url"] == "/outputs/request/NDWI/ndwi.tif"
    assert frontend["preview_url"] == "/outputs/request/NDWI/ndwi_preview.png"
    assert frontend["visualization"]["minimum"] == -1.0
    assert frontend["visualization"]["maximum"] == 1.0
    assert frontend["visualization"]["legend"][0]["label"].startswith("Dry")
    assert result["frontend"]["request_metadata_url"] is None

def test_request_defaults_to_sentinel2():
    model = app_module.EOProcessRequest(**BASE_REQUEST)

    assert model.source == "sentinel2"


def test_products_endpoint_supports_source_selection():
    result = app_module.supported_products(source="sentinel2")

    assert result["source"] == "sentinel2"
    assert result["implemented"] is True
    assert set(result["products"]) == set(app_module.get_supported_products("sentinel2"))


def test_products_endpoint_rejects_unknown_source():
    with pytest.raises(HTTPException) as exc_info:
        app_module.supported_products(source="unknown")

    assert exc_info.value.status_code == 404
    
    
