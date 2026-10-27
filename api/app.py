"""FastAPI interface for the AFROSAT EO processing pipeline."""

import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from eo.api import run_processing
from eo.pipeline import run_eo_pipeline
from eo.tiled_pipeline import OUTPUT_NAMES


app = FastAPI(
    title="AFROSAT EO Processing API",
    version="1.1.0",
    description=(
        "Programmatic Sentinel-2 processing for AFROSAT. "
        "Supports single-product and multi-product requests."
    ),
)
# Local development: serve generated files from the project's outputs folder.
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app.mount(
    "/outputs",
    StaticFiles(directory=str(OUTPUTS_DIR)),
    name="outputs",
)


def _output_url(path):
    """Return a local HTTP URL only for an existing file under outputs/."""
    if not path:
        return None

    try:
        candidate = Path(path).resolve()
        relative_path = candidate.relative_to(OUTPUTS_DIR.resolve())
    except (OSError, ValueError):
        # Do not expose paths outside the configured output directory.
        return None

    if not candidate.is_file():
        return None

    return f"/outputs/{quote(relative_path.as_posix(), safe='/')}"


def _add_frontend_links(result):
    """Add browser-facing output URLs without removing existing result fields."""

    def add_product_links(product_result):
        if product_result.get("status") != "completed":
            return

        process = str(product_result.get("process", "")).upper()
        product_path = product_result.get("product_path")
        preview_path = None

        if process in {"RGB", "FALSE_COLOR"} and product_path:
            candidate = (
                Path(product_path).parent
                / f"{process.lower()}_preview.png"
            )
            if candidate.is_file():
                preview_path = candidate

        product_result["frontend"] = {
            "product_url": _output_url(product_path),
            "metadata_url": _output_url(
                product_result.get("metadata_path")
            ),
            "manifest_url": _output_url(
                product_result.get("manifest_path")
            ),
            "valid_count_url": _output_url(
                product_result.get("valid_count_path")
            ),
            "preview_url": _output_url(preview_path),
        }

    # Multi-product response.
    products = result.get("products")
    if isinstance(products, dict):
        for product_result in products.values():
            if isinstance(product_result, dict):
                add_product_links(product_result)

        result["frontend"] = {
            "request_metadata_url": _output_url(
                result.get("request_metadata_path")
            )
        }
        return result

    # Single-product response.
    if result.get("product_path"):
        add_product_links(result)

    return result


class EOProcessRequest(BaseModel):
    """Validated request body for EO processing."""

    model_config = ConfigDict(extra="forbid")

    aoi: dict[str, Any] = Field(
        ...,
        description="GeoJSON Polygon or MultiPolygon geometry in WGS84.",
    )
    start_date: date
    end_date: date

    # Keep the original single-product interface.
    process: str = Field(
        default="NDVI",
        description="One product, used when products is not supplied.",
    )

    # When supplied, this takes precedence over process.
    products: list[str] | None = Field(
        default=None,
        description=(
            "One or more supported products. "
            "The supported list is derived from the processing pipeline."
        ),
    )

    composite_method: Literal["median", "mean"] = "median"
    cloud_cover_threshold: float = Field(default=20, ge=0, le=100)
    tile_size_m: int = Field(default=5000, gt=0)
    processing_mode: Literal[
        "auto",
        "single_window",
        "tiled",
    ] = "auto"
    max_window_size_m: float = Field(default=10000, gt=0)
    max_workers: int = Field(default=2, ge=1, le=8)
    tile_retries: int = Field(default=3, ge=0)
    write_valid_count: bool = True
    keep_tiles: bool = True
    scl_retained_classes: list[int] | None = None
    output_dir: str | None = None
    input_parameters: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health():
    """Lightweight health check; does not access satellite data."""
    return {"status": "ok", "service": "afrosat-eo"}


@app.get("/products")
def supported_products():
    """Return the products currently supported by the processing pipeline."""
    return {"products": list(OUTPUT_NAMES)}


@app.post("/process")
async def process(request: EOProcessRequest):
    """Submit an AOI for single-product or multi-product processing."""

    if request.start_date > request.end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date.",
        )

    common_options = {
        "composite_method": request.composite_method,
        "tile_size_m": request.tile_size_m,
        "processing_mode": request.processing_mode,
        "max_window_size_m": request.max_window_size_m,
        "max_workers": request.max_workers,
    }

    try:
        if request.products is not None:
            # The multi-product wrapper validates products and shares a scene search.
            result = await run_in_threadpool(
                run_processing,
                aoi=request.aoi,
                start_date=request.start_date.isoformat(),
                end_date=request.end_date.isoformat(),
                products=request.products,
                max_cloud_cover=request.cloud_cover_threshold,
                output_dir=request.output_dir,
                tile_retries=request.tile_retries,
                write_valid_count=request.write_valid_count,
                keep_tiles=request.keep_tiles,
                scl_retained_classes=request.scl_retained_classes,
                **common_options,
            )

            # The wrapper reports request-level validation failures
            # as structured results rather than raising exceptions.
            if result.get("status") == "failed" and any(
                error.get("code") == "INVALID_REQUEST"
                for error in result.get("errors", [])
            ):
                raise HTTPException(status_code=422, detail=result)

            return _add_frontend_links(result)

        # Preserve the original single-product endpoint and response.
        return await run_in_threadpool(
            run_eo_pipeline,
            aoi_geometry=request.aoi,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            process=request.process,
            composite_method=request.composite_method,
            cloud_cover_threshold=request.cloud_cover_threshold,
            output_dir=request.output_dir,
            tile_size_m=request.tile_size_m,
            processing_mode=request.processing_mode,
            max_window_size_m=request.max_window_size_m,
            max_workers=request.max_workers,
            tile_retries=request.tile_retries,
            write_valid_count=request.write_valid_count,
            keep_tiles=request.keep_tiles,
            scl_retained_classes=request.scl_retained_classes,
            input_parameters=request.input_parameters,
        )

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
