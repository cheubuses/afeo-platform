"""Tests for the EO source registry."""

import pytest

from eo.registry import (
    get_source_config,
    get_supported_products,
)


def test_sentinel2_is_registered_and_implemented():
    config = get_source_config("sentinel2")

    assert config.implemented is True
    assert set(get_supported_products("sentinel2")) == {
        "RGB",
        "FALSE_COLOR",
        "NDVI",
        "NDWI",
        "NDMI",
        "NBR",
        "NBR2",
    }


@pytest.mark.parametrize("source", ["landsat", "tivion"])
def test_future_sources_are_registered_but_not_implemented(source):
    config = get_source_config(source)

    assert config.implemented is False
    assert get_supported_products(source) == {}


def test_source_names_are_case_insensitive():
    assert get_source_config("SENTINEL2").name == "sentinel2"


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="Unsupported source"):
        get_source_config("unknown")
