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
from eo.registry import get_source_config, get_supported_products
from eo.tiled_pipeline import OUTPUT_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from eo.api import run_processing
from eo.tivion.inspection import inspect_airsat05, validate_airsat05
from eo.tivion.processor import process_airsat05



app = FastAPI(
    title="AFROSAT EO Processing API",
    version="1.1.0",
    description=(
        "AFROSAT Earth Observation Processing API. "
        "Provides Sentinel-2 optical processing and a "
        "TIVION/AIRSAT-05 SAR request interface."
    ),
    openapi_tags=[
        {
            "name": "Sentinel-2",
            "description": "Operational Sentinel-2 optical image processing.",
        },
        {
            "name": "TIVION SAR",
            "description": (
                "TIVION/AIRSAT-05 SAR processing options. "
                "Processing functionality is under development."
            ),
        },
    ],
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
    """Add browser URLs and frontend visualization hints to product results."""

    def add_product_links(product_result):
        if product_result.get("status") != "completed":
            return

        process = str(
            product_result.get("process")
            or product_result.get("product")
            or ""
        ).upper()
        product_path = product_result.get("product_path")
        preview_path = product_result.get("preview_path")

        if not preview_path and product_path:
            preview_name = {
                "RGB": "rgb_preview.png",
                "FALSE_COLOR": "false_color_preview.png",
                "NDVI": "ndvi_preview.png",
                "NDWI": "ndwi_preview.png",
            }.get(process)
            if preview_name:
                candidate = Path(product_path).parent / preview_name
                if candidate.is_file():
                    preview_path = str(candidate)

        frontend = {
            "product_url": _output_url(product_path),
            "metadata_url": _output_url(product_result.get("metadata_path")),
            "manifest_url": _output_url(product_result.get("manifest_path")),
            "valid_count_url": _output_url(product_result.get("valid_count_path")),
            "preview_url": _output_url(preview_path),
        }

        if process == "NDWI":
            frontend["visualization"] = {
                "type": "singleband-pseudocolor",
                "band": 1,
                "minimum": -1.0,
                "maximum": 1.0,
                "palette": [
                    "#8c510a", "#d8b365", "#f6e8c3",
                    "#c7eae5", "#5ab4ac", "#01665e",
                ],
                "legend": [
                    {"value": -1.0, "label": "Dry / negative NDWI"},
                    {"value": 0.0, "label": "Near zero"},
                    {"value": 1.0, "label": "Water / positive NDWI"},
                ],
                "units": "unitless",
                "note": "Display styling only; use the COG for analysis.",
            }

        product_result["frontend"] = frontend

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

    if result.get("product_path"):
        add_product_links(result)
    return result


class EOProcessRequest(BaseModel):
    """Validated request body for EO processing."""
    source: str = Field(
        default="sentinel2",
        description="EO data source: sentinel2, landsat or tivion.",
    )

    model_config = ConfigDict(extra="forbid")
    aoi: dict
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

class TivionProcessRequest(BaseModel):
    """Request model for TIVION/AIRSAT-05 SAR processing."""

    model_config = ConfigDict(extra="forbid")

    input_file: str = Field(
        description="Path to the original AIRSAT-05 SAR TIFF product."
    )
    operation: Literal[
    "inspect",
    "validate",
    "process",
    ] | None = Field(
        default=None,
        description=(
            "TIVION operation. If omitted, the legacy "
            "georeferencing setting determines inspect or validate."
        ),
    )

    output_dir: str | None = Field(
        default=None,
        description=("Optional output directory. If omitted, the processor creates a default scene/date directory."),
    )

    metadata_file: str | None = Field(
        default=None,
        description="Optional AIRSAT-05 XML metadata file."
    )

    aoi: dict | None = Field(
        default=None,
        description="Optional AOI in GeoJSON format."
    )

    product: Literal[
        "SAR_INTENSITY",
        "BACKSCATTER",
        "SAR_RGB",
    ] = "SAR_INTENSITY"

    polarisation: Literal[
        "HH",
        "HV",
        "VV",
        "VH",
    ] = "HH"

    speckle_filter: Literal[
        "none",
        "lee",
        "refined_lee",
        "gamma_map",
    ] = "none"

    georeferencing: Literal[
        "inspect",
        "validate",
        "apply_correction",
    ] = "inspect"

    output_format: Literal[
        "GTiff",
        "COG",
    ] = "COG"


@app.get("/health")
def health():
    """Lightweight health check; does not access satellite data."""
    return {"status": "ok", "service": "afrosat-eo"}


@app.get("/products", tags=["Sentinel-2"])
def supported_products(source: str = "sentinel2"):
    """Return the products registered for the requested data source."""

    try:
        config = get_source_config(source)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
    "source": config.name,
    "implemented": config.implemented,
    "generic_pipeline": config.generic_pipeline,
    "description": config.description,
    "products": list(get_supported_products(config.name)),
}


@app.post("/process", tags=["Sentinel-2"])
async def process(request: EOProcessRequest):
    """Submit an AOI for single-product or multi-product processing."""

    if request.start_date > request.end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date.",
        )

    requested_products = (
        request.products
        if request.products is not None
        else [request.process]
    )
    common_options = {
        "composite_method": request.composite_method,
        "tile_size_m": request.tile_size_m,
        "processing_mode": request.processing_mode,
        "max_window_size_m": request.max_window_size_m,
        "max_workers": request.max_workers,
    }

    try:
        # Both one-product and multi-product requests use the same response
        # contract and the same shared-scene search implementation.
        result = await run_in_threadpool(
            run_processing,
            aoi=request.aoi,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            products=requested_products,
            max_cloud_cover=request.cloud_cover_threshold,
            output_dir=request.output_dir,
            source=request.source,
            tile_retries=request.tile_retries,
            write_valid_count=request.write_valid_count,
            keep_tiles=request.keep_tiles,
            scl_retained_classes=request.scl_retained_classes,
            **common_options,
        )

        if result.get("status") == "failed" and any(
            error.get("code") == "INVALID_REQUEST"
            for error in result.get("errors", [])
        ):
            raise HTTPException(status_code=422, detail=result)

        # Preserve a simple single-product alias while retaining the common
        # products dictionary for frontend code that handles either request.
        if request.products is None:
            result["process"] = str(request.process).strip().upper()
        return _add_frontend_links(result)

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    
@app.post("/process/tivion", tags=["TIVION SAR"])
async def process_tivion(request: TivionProcessRequest):
    """
    Inspect, validate or process TIVION/AIRSAT-05 SAR imagery.

    TIVION processing is independent of the Sentinel-2 STAC workflow.
    """

    # Preserve the existing inspect/validate behavior when operation
    # is omitted from older requests.
    operation = request.operation

    if operation is None:
        operation = (
            "inspect"
            if request.georeferencing == "inspect"
            else "validate"
        )

    if not request.metadata_file:
        raise HTTPException(
            status_code=422,
            detail="metadata_file is required for TIVION operations.",
        )

    if operation == "process":

        # Only expose functionality that the current processor
        # actually implements and has been tested to preserve.
        if request.product != "SAR_INTENSITY":
            raise HTTPException(
                status_code=501,
                detail=(
                    f"{request.product} is not implemented. "
                    "Currently supported: SAR_INTENSITY."
                ),
            )

        if request.polarisation != "HH":
            raise HTTPException(
                status_code=501,
                detail=(
                    "The current processor has only been validated "
                    "with HH polarisation."
                ),
            )

        if request.speckle_filter != "none":
            raise HTTPException(
                status_code=501,
                detail=(
                    "Speckle filtering is not implemented yet. "
                    "Use speckle_filter='none'."
                ),
            )

        if request.output_format != "COG":
            raise HTTPException(
                status_code=501,
                detail=(
                    "Only COG output is currently implemented."
                ),
            )

        if request.georeferencing == "apply_correction":
            raise HTTPException(
                status_code=501,
                detail=(
                    "Georeferencing correction is not implemented."
                ),
            )

        try:
            result = await run_in_threadpool(
                process_airsat05,
                request.input_file,
                request.metadata_file,
                request.output_dir,
            )

            return result

        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"TIVION processing failed: {exc}",
            ) from exc

    # Inspection and validation operations.
    if request.georeferencing == "apply_correction":
        raise HTTPException(
            status_code=501,
            detail="Georeferencing correction is not implemented yet.",
        )

    inspection_operation = (
        inspect_airsat05
        if operation == "inspect"
        else validate_airsat05
    )

    try:
        return await run_in_threadpool(
            inspection_operation,
            request.input_file,
            request.metadata_file,
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc