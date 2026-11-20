"""Tests for the EO source registry."""

import pytest

from eo.registry import (
    get_source_config,
    get_supported_products,
)


def test_sentinel2_is_registered_and_implemented():
    config = get_source_config("sentinel2")

    assert config.implemented is True
    assert config.generic_pipeline is True

    assert set(get_supported_products("sentinel2")) == {
        "RGB",
        "FALSE_COLOR",
        "NDVI",
        "NDWI",
        "NDMI",
        "NBR",
        "NBR2",
    }


def test_airsat_is_implemented_with_dedicated_pipeline():
    config = get_source_config("airsat")

    assert config.implemented is True
    assert config.generic_pipeline is False

    assert get_supported_products("airsat") == {
        "SAR_INTENSITY": "sar_hh_cog.tif"
    }


def test_landsat_is_registered_but_not_implemented():
    config = get_source_config("landsat")

    assert config.implemented is False
    assert get_supported_products("landsat") == {}


def test_source_names_are_case_insensitive():
    assert get_source_config("SENTINEL2").name == "sentinel2"


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="Unsupported source"):
        get_source_config("unknown")