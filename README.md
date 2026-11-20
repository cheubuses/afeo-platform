# AFROSAT Earth Observation Processing Platform

## Technical Documentation — 20 November 2026

## 1. Project Overview
AFROSAT is a Python-based Earth Observation (EO) processing platform for accessing, processing, validating and managing satellite imagery from multiple sources.
It currently contains two independent workflows:
1. Sentinel-2 Optical Processing — accesses Sentinel-2 Level-2A imagery through a SpatioTemporal Asset Catalog (STAC), applies Scene Classification Layer (SCL) masking, builds temporal composites and produces optical indices and image composites.
2. AIRSAT-05 SAR Processing — ingests AIRSAT-05 Synthetic Aperture Radar (SAR) imagery, inspects raster and XML metadata, validates geospatial consistency and generates Cloud Optimized GeoTIFFs (COGs) with supporting metadata and previews.
The platform is built from reusable Python modules and a FastAPI application. Results are written to request- or scene-specific output directories with structured metadata for downstream applications and frontend integration.

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
       Sentinel-2 Optical   AIRSAT SAR
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

## 4. AIRSAT-05 SAR Processing

The AIRSAT-05 workflow handles AIRSAT-05 SAR imagery supplied as a raster TIFF and associated XML metadata.

The AIRSAT workflow supports metadata inspection, geospatial validation, preservation of source pixel values, COG generation and supporting map assets. It does not independently perform radiometric calibration or terrain correction.

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
2. Extracts the full vendor product identifier and acquisition date.
3. Preserves copies of the original TIFF and XML.
4. Generates a scientific COG while preserving source pixel values.
5. Generates a map COG with an internal validity mask for zero-valued background pixels.
6. Creates a display-stretched JPEG preview and thumbnail.
7. Creates normalized technical metadata and a GeoJSON footprint.
8. Generates a manifest containing asset paths, file sizes and SHA-256 checksums.
9. Validates COG structure, pixel values and the map COG validity mask.
10. Quantitatively compares the raster extent with the vendor scene footprint.

### 4.3 Technical characteristics and limitations

The currently implemented processing configuration is:

- Product: `SAR_INTENSITY`
- Polarisation: `HH`
- Speckle filter: `none`
- Output format: `COG`

**Raster and spatial characteristics**

- The supplied raster is single-band UInt16 in EPSG:4326.
- The vendor identifies the product as L2 GEC (Geocoded Ellipsoid Corrected).
- Vendor-confirmed nominal spatial resolution is 0.5 m.
- Output pixel spacing is approximately 0.5025935 m in both dimensions.
- The XML nominal resolution value of 0.135346 is retained without interpreting it as ground resolution.
- The processor preserves the supplied geographic grid and does not perform additional reprojection or terrain correction.

**Radiometric characteristics**

- Stored values represent linear SAR amplitude digital numbers (DN), not decibels.
- The scientific COG preserves the original stored pixel values.
- The processor does not establish calibrated sigma0, gamma0 or beta0 products.
- The XML calibration constant and radiometric model are retained, but their meaning and validity require vendor confirmation.
- Vendor XML correction flags are reported as metadata; the processor does not independently recalculate or verify those corrections.

**Background pixels, previews and footprints**

- Vendor-confirmed zero-valued background pixels are marked invalid in the map COG's internal validity mask.
- This mask does not change scientific COG pixel values or assign a scientific NoData value.
- JPEG previews use a display-only intensity stretch and are not calibrated SAR products.
- The raster extent and vendor scene footprint are retained separately and quantitatively compared.

## 5. FastAPI Interface

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
| `/process/airsat` | POST | Inspect, validate or process AIRSAT SAR data |

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

### 5.3 Sentinel-2 optical response example

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

### 5.4 AIRSAT-05/SAR request example

The SAR endpoint requires an input TIFF and its associated XML metadata file.

```json
{
  "input_file": "data/airsat05_sample.tiff",
  "metadata_file": "data/airsat05_sample.xml",
  "operation": "process",
  "product": "SAR_INTENSITY",
  "polarisation": "HH",
  "speckle_filter": "none",
  "georeferencing": "inspect",
  "output_format": "COG"
}
```

The currently implemented processing combination is `SAR_INTENSITY`, `HH`, no speckle filter and COG output. Unsupported processing options are rejected rather than silently applied.

The example abbreviates filesystem paths for readability; the API returns the generated output directory and asset paths for the local execution environment.

Example successful response:

```json
{
  "status": "completed",
  "source": "airsat",
  "product": "SAR_INTENSITY",
  "polarisation": "HH",
  "product_id": "PRODUCT_ID",
  "acquisition_date": "YYYY-MM-DD",
  "output_directory": "outputs/imagery/airsat/{product_id}/{acquisition_date}",
  "technical_metadata": {
    "spatial_resolution": {
      "vendor_confirmed_nominal_spatial_resolution_m": 0.5,
      "output_pixel_spacing_m": {
        "width": 0.5025935,
        "height": 0.5025935
      }
    },
    "georeferencing": {
      "correction_level": "GEC",
      "terrain_correction_applied": false,
      "output_crs": "EPSG:4326"
    },
    "radiometry": {
      "units": "DN",
      "value_scale": "Linear, not decibels (dB)"
    }
  },
  "assets": {
    "source_original": "source/original.tiff",
    "source_metadata_xml": "source/original.meta.xml",
    "sar_hh_cog": "cog/sar_hh_cog.tif",
    "sar_hh_map_cog": "cog/sar_hh_map_cog.tif",
    "sar_hh_preview": "previews/sar_hh_preview.jpg",
    "sar_hh_thumbnail": "previews/sar_hh_thumbnail.jpg",
    "metadata": "metadata/metadata.json",
    "footprint": "metadata/footprint.geojson",
    "manifest": "manifest.json"
  },
  "validation": {
    "cog": {
      "pixel_values_identical": true
    },
    "map_cog": {
      "pixel_values_identical": true,
      "internal_mask_validated": true
    },
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

### 6.2 AIRSAT-05 outputs

Each AIRSAT product is stored in a directory identified by its full vendor product ID and acquisition date.

```text
outputs/imagery/airsat/{product_id}/{acquisition_date}/
├── manifest.json
├── source/
│   ├── original.tiff
│   └── original.meta.xml
├── cog/
│   ├── sar_hh_cog.tif
│   └── sar_hh_map_cog.tif
├── previews/
│   ├── sar_hh_preview.jpg
│   └── sar_hh_thumbnail.jpg
└── metadata/
    ├── metadata.json
    └── footprint.geojson
```

The scientific COG preserves source pixel values. The map COG uses an internal validity mask for zero-valued background pixels. The manifest records asset paths, file sizes, SHA-256 checksums and generation time.

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
.venv/bin/uvicorn api.app:app --reload
```

### 7.3 Run tests

```bash
.venv/bin/python -m pytest -q
```

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
- Source NoData preservation and map COG validity-mask checks.
- COG structure inspection.
- Pixel-by-pixel comparison of source values with the scientific and map COGs.

A successful COG conversion does not establish that the source data are radiometrically calibrated or terrain corrected. These are separate scientific processing requirements.

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
│       ├── airsat/
│       │   ├── processor.py
│       │   ├── inspection.py
│       │   └── footprint.py
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
| `src/eo/airsat/processor.py` | SAR COG conversion and asset generation |
| `src/eo/airsat/inspection.py` | SAR inspection and geospatial validation |
| `src/eo/airsat/footprint.py` | Raster and vendor footprint comparison metrics |
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

- Establish vendor-supported radiometric calibration and validate its outputs.
- Implement terrain correction where supported by the source data and metadata.
- Extend support for additional polarisations and SAR products.
- Implement and validate speckle filtering.
- Extend footprint and geospatial validation to additional SAR products and acquisition geometries.
