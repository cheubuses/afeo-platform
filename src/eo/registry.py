"""Registry of EO data sources and their supported products."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceConfig:
    """Configuration describing one registered EO data source."""

    name: str
    products: dict[str, str]
    implemented: bool
    description: str
    generic_pipeline: bool = True


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
        generic_pipeline=True,
    ),

    "landsat": SourceConfig(
        name="landsat",
        products={},
        implemented=False,
        description="Landsat processing is planned but not implemented.",
        generic_pipeline=True,
    ),

    "tivion": SourceConfig(
        name="tivion",
        products={
            "SAR_INTENSITY": "sar_hh_cog.tif",
        },
        implemented=True,
        description=(
            "TIVION/AIRSAT-05 SAR intensity processing "
            "for validated HH-polarised products."
        ),
        generic_pipeline=False,
    ),
}


def normalize_source(source):
    """Normalize a source identifier."""
    return str(source).strip().lower()


def get_source_config(source):
    """Return the configuration for one registered EO source."""

    source_name = normalize_source(source)

    if source_name not in SOURCES:
        supported = ", ".join(sorted(SOURCES))
        raise ValueError(
            f"Unsupported source {source_name!r}. "
            f"Registered sources: {supported}."
        )

    return SOURCES[source_name]


def get_supported_products(source):
    """Return a copy of the product catalogue."""
    return dict(get_source_config(source).products)
