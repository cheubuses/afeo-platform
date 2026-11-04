# AFROSAT Earth Observation Processing Platform

# Technical documentation — 4 November 2026.  

1. Project Overview
AFROSAT is a Python-based Earth Observation (EO) processing platform for accessing, processing, validating and managing satellite imagery from multiple sources.
It currently contains two independent workflows:
1. Sentinel-2 Optical Processing — accesses Sentinel-2 Level-2A imagery through a SpatioTemporal Asset Catalog (STAC), applies Scene Classification Layer (SCL) masking, builds temporal composites and produces optical indices and image composites.
2. TIVION/AIRSAT-05 SAR Processing — ingests AIRSAT-05 Synthetic Aperture Radar (SAR) imagery, inspects raster and XML metadata, validates geospatial consistency and generates Cloud Optimized GeoTIFFs (COGs) with supporting metadata and previews.
The platform is built from reusable Python modules and a FastAPI application. Results are written to request- or scene-specific output directories with structured metadata for downstream applications and frontend integration.


The platform is designed around reusable Python processing modules, with FastAPI providing an interface for submitting requests and accessing results.

## 2. Architecture

The platform separates request handling, source-specific processing and output validation.

```text
                  User / Client
                       |
                       v
                  FastAPI API
                       |
             Request validation
                       |
             Source identification
                 /         \
                /           \
               v             v
       Sentinel-2 Optical   TIVION SAR
               |             |
        STAC search       TIFF + XML
               |             |
        Scene selection   Inspection /
               |          Validation /
        SCL masking       COG conversion
               |             |
        Temporal composite   |
               |             |
        Spectral products    |
               \             /
                \           /
                 v         v
             Output assets
             Metadata and
             validation results
```

The two processing workflows have different input requirements and processing capabilities. They share the same API environment but do not use identical processing methods.

## 3. Sentinel-2 Optical Processing

### 3.1 Data access and AOI definition

Sentinel-2 Level-2A imagery is accessed through the Earth Search STAC API:

- STAC endpoint: `https://earth-search.aws.element84.com/v1`
- Collection: `sentinel-2-c1-l2a`
- Access: `pystac-client` and `odc-stac`
- Raster processing: Xarray, Dask and Rasterio

The AOI can be supplied as a WGS84 GeoJSON Polygon or MultiPolygon. The interactive workflow also supports administrative boundaries from FieldMaps GeoParquet data and coordinate-based AOIs.

### 3.2 Processing workflow

1. Validate the AOI, dates, cloud threshold and requested products.
2. Search the STAC catalogue for Sentinel-2 scenes intersecting the AOI.
3. Filter scenes by acquisition date and cloud cover.
4. Select a reference scene and load the required spectral bands.
5. Apply reflectance scaling and Scene Classification Layer (SCL) masking.
6. Generate a median or mean temporal composite.
7. Calculate the requested spectral products.
8. Process the AOI using single-window or tiled processing.
9. Export Cloud Optimized GeoTIFFs (COGs).
10. Generate metadata, validation information and available previews.

The platform searches for scenes once per multi-product request and reuses the selected scene collection across the requested products.

### 3.3 Reflectance scaling and cloud masking

Sentinel-2 Level-2A surface reflectance values require appropriate scale and offset handling.

The implementation uses STAC raster metadata and the applicable BOA offset information. Reflectance scaling must be applied consistently: offsets must neither be omitted when required nor applied twice.

The index calculations expect appropriately scaled reflectance inputs. They do not perform a second scaling operation.

The Scene Classification Layer (SCL) is used to exclude unsuitable observations. The default retained classes are:

| SCL class | Description |
|---|---|
| 4 | Vegetation |
| 5 | Non-vegetated land |
| 6 | Water |

Other classes, including cloud, cirrus, cloud shadow, saturated and defective pixels, are excluded by default.

The masking workflow tracks eligible observations, retained observations, rejected observations and valid observation counts. A valid-observation-count raster can also be generated when requested.

### 3.4 Available optical products

| Product | Bands / calculation | Description |
|---|---|---|
| RGB | B04, B03, B02 | Natural-colour composite |
| FALSE_COLOR | B08, B04, B03 | NIR false-colour composite |
| NDVI | `(B08 - B04) / (B08 + B04)` | Vegetation condition |
| NDWI | `(B03 - B08) / (B03 + B08)` | Water-related spectral response |
| NDMI | `(B08 - B11) / (B08 + B11)` | Vegetation moisture |
| NBR | `(B08 - B12) / (B08 + B12)` | Burn severity and vegetation disturbance |
| NBR2 | `(B11 - B12) / (B11 + B12)` | Moisture and burn-related analysis |

Index calculations mask invalid or unsuitable reflectance values and handle near-zero denominators.

### 3.5 Tiling and raster export

Large AOIs can be processed using spatial tiles to manage memory consumption.

Important processing parameters include:

- `tile_size_m`: target tile size in metres.
- `processing_mode`: `auto`, `single_window` or `tiled`.
- `max_window_size_m`: maximum processing window.
- `max_workers`: parallel processing limit.
- `tile_retries`: retry attempts for failed tiles.
- `keep_tiles`: whether intermediate tile outputs are retained.

Final optical products are exported as Cloud Optimized GeoTIFFs. COG validation checks the output structure and raster properties.

## 4. TIVION / AIRSAT-05 SAR Processing 

The TIVION workflow handles AIRSAT-05 SAR imagery supplied as a raster TIFF and associated XML metadata.

Unlike the Sentinel-2 workflow, the current SAR implementation focuses on inspection, validation and preservation of the supplied raster values during COG conversion. It does not yet implement a complete SAR calibration and terrain-correction workflow.

### 4.1 Supported operations

| Operation | Function | Purpose |
|---|---|---|
| `inspect` | `inspect_airsat05()` | Read raster and XML metadata and report potential issues |
| `validate` | `validate_airsat05()` | Check geospatial consistency between raster and XML |
| `process` | `process_airsat05()` | Convert the supplied SAR raster to a validated COG and generate supporting assets |

Inspection and validation are metadata-oriented operations. They do not modify or reprocess the source raster.

### 4.2 Processing workflow

The processing operation:

1. Validates the input TIFF and XML files.
2. Extracts the scene identifier and acquisition date.
3. Preserves copies of the original TIFF and XML.
4. Converts the raster to Cloud Optimized GeoTIFF.
5. Generates a grayscale preview and thumbnail.
6. Creates metadata and a geographic footprint.
7. Generates a manifest containing asset paths, file sizes and SHA-256 checksums.
8. Validates the output COG against the source raster.

### 4.3 SAR processing limitations

The current implementation supports the following processing configuration:

- Product: `SAR_INTENSITY`
- Polarisation: `HH`
- Speckle filter: `none`
- Output format: `COG`

The COG conversion preserves the original stored pixel values. Validation compares the source and output pixel values to confirm that conversion has not changed them.

Important scientific limitations:

- Radiometric calibration has not been independently implemented or verified.
- Terrain correction has not been implemented.
- The preview uses a display stretch and is not calibrated backscatter.
- Original zero values are preserved and are not automatically interpreted as NoData.
- The geographic footprint is derived from raster bounds.
- Metadata warnings may be returned for missing NoData, calibration information or inconsistencies between the TIFF and XML.

The presence of request-model options for other products, polarisations, speckle filters or georeferencing corrections does not mean those capabilities are implemented.

## 5. FastAPI Interface

Start the API from the project root:

```bash
python -m uvicorn api.app:app --reload
```

The local service is available at:

```text
http://127.0.0.1:8000
```

Interactive API documentation:

```text
http://127.0.0.1:8000/docs
```

### 5.1 Available endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Check API availability |
| `/products` | GET | Retrieve registered sources and products |
| `/process` | POST | Submit Sentinel-2 optical processing requests |
| `/process/tivion` | POST | Inspect, validate or process TIVION SAR data |

### 5.2 Sentinel-2 request example

The optical endpoint accepts either one product through `process` or multiple products through `products`.

```json
{
  "source": "sentinel2",
  "aoi": {
    "type": "Polygon",
    "coordinates": [
      [
        [36.0, 2.0],
        [36.1, 2.0],
        [36.1, 2.1],
        [36.0, 2.1],
        [36.0, 2.0]
      ]
    ]
  },
  "start_date": "2026-02-01",
  "end_date": "2026-02-28",
  "products": ["NDVI", "NDWI", "RGB"],
  "composite_method": "median",
  "cloud_cover_threshold": 20,
  "tile_size_m": 5000,
  "processing_mode": "auto"
}
```

The request can also include processing controls such as `max_workers`, `tile_retries`, `keep_tiles`, `write_valid_count` and `output_dir`.

### 5.3 Optical/Sentinel 2 response example

The API returns an overall request status and individual product results.

```json
{
  "request_id": "ddfd463984a142ec80f607d5edb55a3f",
  "status": "completed",
  "source": "sentinel2",
  "requested_products": ["NDVI", "NDWI", "RGB"],
  "completed_products": 3,
  "failed_products": 0,
  "scene_count": 1,
  "products": {
    "NDVI": {
      "status": "completed",
      "product_path": "ndvi.tif",
      "metadata_path": "ndvi_metadata.json"
    },
    "NDWI": {
      "status": "completed",
      "product_path": "ndwi.tif",
      "metadata_path": "ndwi_metadata.json"
    },
    "RGB": {
      "status": "completed",
      "product_path": "rgb.tif",
      "metadata_path": "rgb_metadata.json"
    }
  },
  "errors": []
}
```

Possible overall statuses are:

- `completed`: all requested products completed.
- `partial_success`: some products completed and others failed.
- `failed`: the request could not produce a successful product.

The API also returns structured errors, including `NO_SCENES_FOUND`, `NO_VALID_PIXELS`, `TILE_PROCESSING_FAILED` and `GDAL_TOOLS_MISSING`.

### 5.4 TIVION/SAR request example

The SAR endpoint requires an input TIFF and its associated XML metadata file.

```json
{
  "input_file": "/path/to/airsat05_scene.tif",
  "metadata_file": "/path/to/airsat05_scene.xml",
  "operation": "process",
  "product": "SAR_INTENSITY",
  "polarisation": "HH",
  "speckle_filter": "none",
  "georeferencing": "inspect",
  "output_format": "COG"
}
```

The currently implemented processing combination is `SAR_INTENSITY`, `HH`, no speckle filter and COG output. Unsupported processing options are rejected rather than silently applied.

Example successful response:

```json
{
  "status": "completed",
  "source": "tivion",
  "product": "SAR_INTENSITY",
  "polarisation": "HH",
  "output_directory": "outputs/tivion/SCENE_ID/2026-10-28",
  "assets": {
    "sar_hh_cog": "processed/sar_hh_cog.tif",
    "sar_hh_preview": "processed/sar_hh_preview.jpg",
    "sar_hh_thumbnail": "processed/sar_hh_thumbnail.jpg",
    "metadata": "metadata/metadata.json",
    "footprint": "metadata/footprint.geojson",
    "manifest": "metadata/manifest.json"
  },
  "validation": {
    "pixel_values_identical": true
  }
}
```

## 6. Output Structure and Metadata

### 6.1 Sentinel-2 outputs

Each optical request receives a separate output directory.

```text
outputs/
└── YYYYMMDD_YYYYMMDD_request-XXXXXXXX/
    ├── request_metadata.json
    ├── NDVI/
    │   ├── ndvi.tif
    │   └── ndvi_metadata.json
    ├── NDWI/
    │   ├── ndwi.tif
    │   └── ndwi_metadata.json
    └── RGB/
        ├── rgb.tif
        └── rgb_metadata.json
```

Additional files, such as previews, valid-observation-count rasters, tile manifests and intermediate tiles, depend on the processing configuration and successful completion of the workflow.

`request_metadata.json` provides a request-level record of the processing operation, while individual product metadata files describe the corresponding outputs.

### 6.2 TIVION outputs

```text
outputs/
└── tivion/
    └── SCENE_ID/
        └── ACQUISITION_DATE/
            ├── source/
            │   └── original.tiff
            ├── processed/
            │   ├── sar_hh_cog.tif
            │   ├── sar_hh_preview.jpg
            │   └── sar_hh_thumbnail.jpg
            └── metadata/
                ├── original.meta.xml
                ├── metadata.json
                ├── footprint.geojson
                └── manifest.json
```

The SAR manifest records the generated assets, relative paths, file sizes, SHA-256 checksums and generation time.

## 7. Installation and Usage

### 7.1 Environment setup

The project uses Python 3.12 or later.

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the project dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

Check the environment:

```bash
python -m pip check
```

### 7.2 Run the API

```bash
python -m uvicorn api.app:app --reload
```

Open the interactive documentation at:

```text
http://127.0.0.1:8000/docs
```

### 7.3 Run tests

```bash
python -m pytest -q
```

The latest reported test run completed with:

```text
49 passed, 9 warnings
```

This is the last reported test result, not a guarantee of the result in a future checkout.

## 8. Quality Assurance and Validation

Quality assurance is performed at both the processing and output levels.

**Optical validation includes:**

- AOI geometry and coordinate reference system checks.
- Date range and cloud-cover validation.
- STAC scene availability checks.
- SCL masking and valid-observation checks.
- Raster dimensions, CRS, bounds, resolution and NoData inspection.
- COG structural validation.
- Product metadata and output-path verification.
- Tile-level processing and failure reporting.

**SAR validation includes:**

- TIFF and XML dimension consistency.
- CRS and affine transform checks.
- Raster bounds compared with the XML footprint.
- Geographic coordinate range checks.
- Calibration and NoData warnings.
- COG structure inspection.
- Pixel-by-pixel comparison between the original raster and converted COG.

A successful COG conversion does not establish that the source data are radiometrically calibrated or terrain corrected. These are separate scientific processing requirements.

Run the complete test suite using:

```bash
python -m pytest -q
```

## 9. Performance and Reliability

The optical workflow uses Xarray and Dask for raster processing and supports tiled processing for larger AOIs.

Processing behaviour can be adjusted through tile size, processing mode, worker count, maximum window size and retry settings.

For larger AOIs, tiled processing can reduce memory pressure, although smaller tiles may increase processing overhead. The appropriate tile size depends on AOI size, available memory, scene count and processing requirements.

The API records individual product outcomes, allowing a multi-product request to return `partial_success` when some products fail.

Where available, tile manifests and intermediate outputs support investigation and recovery of failed processing runs.

AOI clipping, output dimensions and the final raster footprint should be checked against the requested AOI during quality assurance. Tiling alone does not guarantee that the final output footprint matches the AOI.

## 10. Project Structure

```text
eo-platform/
├── api/
│   └── app.py
├── src/
│   └── eo/
│       ├── api.py
│       ├── registry.py
│       ├── pipeline.py
│       ├── tiled_pipeline.py
│       ├── processor.py
│       ├── indices.py
│       ├── tivion/
│       │   ├── processor.py
│       │   └── inspection.py
│       └── ...
├── notebooks/
│   └── aoi_test.py
├── tests/
├── outputs/
├── pyproject.toml
├── pytest.ini
└── README.md
```

The main responsibilities are:

| Module | Responsibility |
|---|---|
| `api/app.py` | FastAPI application and request models |
| `src/eo/api.py` | Optical multi-product request orchestration |
| `src/eo/registry.py` | Source and product registration |
| `src/eo/pipeline.py` | Optical product processing entry point |
| `src/eo/tiled_pipeline.py` | Optical AOI tiling and processing |
| `src/eo/indices.py` | Spectral index calculations |
| `src/eo/tivion/processor.py` | SAR COG conversion and asset generation |
| `src/eo/tivion/inspection.py` | SAR inspection and geospatial validation |
| `tests/` | Automated tests |

## 11. Current Limitations and Development Priorities

The platform has working optical and SAR workflows, but several areas require further development and verification.

**Optical priorities**

- Verify reflectance scale and offset handling against the source STAC metadata and pixel-value convention.
- Confirm that final raster footprints and dimensions correctly represent the requested AOI.
- Continue validating SCL masking and valid-observation statistics.
- Improve performance for large AOIs and multi-product requests.
- Continue testing tile failure recovery and output consistency.

**SAR priorities**

- Implement and validate radiometric calibration.
- Implement terrain correction where supported by the source data and metadata.
- Extend support for additional polarisations and SAR products.
- Implement and validate speckle filtering.
- Improve geospatial correction and validation capabilities.

**Registry consistency**

The current `src/eo/registry.py` registers TIVION as `implemented=False` with no products, even though the `/process/tivion` endpoint and SAR processing modules are implemented. The registry should be updated so that source discovery and product reporting accurately reflect the available SAR functionality.

Landsat processing is also registered as planned but is not yet implemented.

---

**Project:** AFROSAT Earth Observation Processing Platform

**Primary technologies:** Python, FastAPI, STAC, Sentinel-2, AIRSAT-05 SAR, Xarray, Dask, Rasterio, GeoPandas and Cloud Optimized GeoTIFFs (COGs).