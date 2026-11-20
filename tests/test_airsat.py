"""Tests for the AIRSAT-05 SAR API endpoint."""

from fastapi.testclient import TestClient

import api.app as app_module


client = TestClient(app_module.app)


VALID_REQUEST = {
    "operation": "process",
    "input_file": "data/airsat05_sample.tiff",
    "metadata_file": "data/airsat05_sample.xml",
    "output_dir": "imagery/airsat/test-output",
    "product": "SAR_INTENSITY",
    "polarisation": "HH",
    "speckle_filter": "none",
    "georeferencing": "inspect",
    "output_format": "COG",
}


def fake_processor(input_file, metadata_file, output_dir):
    """Return a predictable successful SAR processing result."""
    return {
        "status": "completed",
        "source": "airsat",
        "product": "SAR_INTENSITY",
        "polarisation": "HH",
        "output_directory": output_dir,
        "assets": {
            "sar_hh_cog": f"{output_dir}/cog/sar_hh_cog.tif"
        },
        "technical_metadata": {
            "georeferencing": {"output_crs": "EPSG:4326"},
            "radiometry": {"units": "DN"},
        },
        "validation": {
            "pixel_values_identical": True
        },
    }


def test_airsat_process_calls_sar_processor(monkeypatch):
    """A valid AIRSAT-05 request calls the SAR processor."""
    calls = []

    def tracked_processor(input_file, metadata_file, output_dir):
        calls.append((input_file, metadata_file, output_dir))
        return fake_processor(input_file, metadata_file, output_dir)

    monkeypatch.setattr(
        app_module,
        "process_airsat05",
        tracked_processor,
    )

    response = client.post("/process/airsat", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["source"] == "airsat"
    assert response.json()["validation"]["pixel_values_identical"] is True
    assert response.json()["technical_metadata"] == {
        "georeferencing": {"output_crs": "EPSG:4326"},
        "radiometry": {"units": "DN"},
    }

    assert calls == [
        (
            VALID_REQUEST["input_file"],
            VALID_REQUEST["metadata_file"],
            VALID_REQUEST["output_dir"],
        )
    ]


def test_airsat_missing_output_directory_uses_default(monkeypatch):
    """A missing output directory is passed to the processor as None."""
    calls = []

    def tracked_processor(input_file, metadata_file, output_dir):
        calls.append((input_file, metadata_file, output_dir))

        # Return a predictable result without processing the real TIFF.
        result = fake_processor(
            input_file,
            metadata_file,
            "mock-default-output",
        )
        result["output_directory"] = "mock-default-output"
        return result

    monkeypatch.setattr(
        app_module,
        "process_airsat05",
        tracked_processor,
    )

    request = {
        **VALID_REQUEST,
        "output_dir": None,
    }

    response = client.post("/process/airsat", json=request)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["output_directory"] == "mock-default-output"

    assert calls == [
        (
            VALID_REQUEST["input_file"],
            VALID_REQUEST["metadata_file"],
            None,
        )
    ]


def test_airsat_unsupported_product_returns_501():
    """BACKSCATTER must not be accepted before implementation."""
    request = {
        **VALID_REQUEST,
        "product": "BACKSCATTER",
    }

    response = client.post("/process/airsat", json=request)

    assert response.status_code == 501
    assert "not implemented" in response.json()["detail"]


def test_airsat_unsupported_speckle_filter_returns_501():
    """Unimplemented speckle filters must be rejected."""
    request = {
        **VALID_REQUEST,
        "speckle_filter": "lee",
    }

    response = client.post("/process/airsat", json=request)

    assert response.status_code == 501
    assert "Speckle filtering" in response.json()["detail"]


def test_airsat_missing_input_file_returns_422(monkeypatch):
    """A missing input file is reported as a client error."""

    def missing_file_processor(*args, **kwargs):
        raise FileNotFoundError("Input TIFF not found")

    monkeypatch.setattr(
        app_module,
        "process_airsat05",
        missing_file_processor,
    )

    response = client.post(
        "/process/airsat",
        json=VALID_REQUEST,
    )

    assert response.status_code == 422
    assert "Input TIFF not found" in response.json()["detail"]
