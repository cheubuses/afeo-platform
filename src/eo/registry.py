"""Registry of EO data sources and their supported products."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceConfig:
    """Configuration describing one registered EO data source."""

    name: str
    products: dict[str, str]
    implemented: bool
    description: str


SOURCES = {
    "sentinel2": SourceConfig(
        name="sentinel2",
        products={
            "RGB": "rgb.tif",
            "FALSE_COLOR": "false_color.tif",
            "NDVI": "ndvi.tif",
            "NDWI": "ndwi.tif",
            "NDMI": "ndmi.tif",
            "NBR": "nbr.tif",
            "NBR2": "nbr2.tif",
        },
        implemented=True,
        description="Sentinel-2 optical imagery processing.",
    ),
    "landsat": SourceConfig(
        name="landsat",
        products={},
        implemented=False,
        description="Landsat processing is planned but not implemented.",
    ),
    "tivion": SourceConfig(
        name="tivion",
        products={},
        implemented=False,
        description="TIVION/AIRSAT-05 SAR processing is planned but not implemented.",
    ),
}


def normalize_source(source):
    """Normalize a source identifier."""
    return str(source).strip().lower()


def get_source_config(source):
    """Return the configuration for a registered source."""
    source_name = normalize_source(source)

    if source_name not in SOURCES:
        supported = ", ".join(sorted(SOURCES))
        raise ValueError(
            f"Unsupported source {source_name!r}. "
            f"Registered sources: {supported}."
        )

    return SOURCES[source_name]


def get_supported_products(source):
    """Return a copy of the product catalogue for a source."""
    return dict(get_source_config(source).products)
