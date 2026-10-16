# Sentinel-2 Cloud-Native Earth Observation Processing

## 1. Project Overview

This project implements a Python-based Earth Observation (EO) processing pipeline that accesses Sentinel-2 Level-2A satellite imagery through a SpatioTemporal Asset Catalog (STAC), processes imagery for a user-defined area of interest (AOI), applies Scene Classification Layer (SCL) masking, creates temporal composites, and exports analysis-ready raster products.

The pipeline is being developed as the processing component of the AFROSAT platform. Its outputs are intended to support downstream applications and integration with a FastAPI backend.

The current workflow supports two products:

- **RGB composite:** A three-band red, green and blue surface-reflectance composite.
- **NDVI:** The Normalized Difference Vegetation Index calculated from red and near-infrared surface reflectance.

Outputs include Cloud Optimized GeoTIFFs (COGs), PNG previews, masking statistics and run metadata.

## 2. Core Features

### AOI Selection

The pipeline supports two AOI input methods.

**Administrative boundaries:** Users can select a country and administrative area from the available FieldMaps GeoParquet data. The current interface supports administrative levels 1, 2 and 3, such as counties, sub-counties and wards.

**Coordinate-based AOI:** Users provide latitude, longitude and a radius in kilometres. The script creates a geographic bounding box around the supplied coordinates.

The AOI is passed to the STAC search and used to constrain the satellite data loading and processing workflow.

### Sentinel-2 Data Access

The pipeline connects to the Earth Search STAC API:

`https://earth-search.aws.element84.com/v1`

It searches Sentinel-2 Level-2A imagery using:

- AOI geometry
- Selected year and month
- Maximum scene cloud-cover threshold

The application reports the matching scene IDs and cloud-cover percentages. It selects a low-cloud scene as a reference while retaining the matching scenes for temporal compositing.

Satellite assets are accessed using `odc-stac`, with unsigned S3 access configured for public cloud-hosted data.

### Temporal Compositing

The current interface supports monthly composites using either:

- **Median:** Calculates the temporal median for each pixel, reducing the influence of outlying observations.
- **Mean:** Calculates the temporal mean for each pixel.

The selected composite method is applied after cloud and invalid-pixel masking.

### Reflectance Processing

The optical bands used are:

| Output band | Sentinel-2 band |
|---|---|
| Red | B04 |
| Green | B03 |
| Blue | B02 |
| Near-infrared | B08 |

The pipeline converts source digital numbers to floating-point surface reflectance using the configured scale factor. The offset treatment is conditional on the Earth Search BOA-offset flag.

**Radiometric validation remains important:** the actual source pixel convention must be confirmed before the conversion is considered fully verified. The source metadata can retain scale and offset fields even when an offset has already been applied to pixel values. The pipeline must avoid both omitting the required correction and applying it twice.

### SCL Cloud and Invalid-Pixel Masking

The Sentinel-2 Scene Classification Layer is used to exclude observations classified as:

`0, 1, 2, 3, 7, 8, 9, 10, 11`

The retained classes are:

- Class 4: Vegetation
- Class 5: Non-vegetated land
- Class 6: Water

Class 2 is excluded to reduce cast-shadow contamination. Class 7 is excluded because it represents unclassified pixels and can include uncertain surface conditions. SCL class descriptions should be consistent with the processing baseline of the source scenes.

The masking module checks SCL alignment against the spectral bands using dimensions, coordinates, CRS and spatial transform where available.

The workflow also calculates:

- Eligible observations
- Observations retained after masking
- Observations rejected by SCL
- Percentage of observations rejected
- Valid observation count per pixel

A `valid_observation_count.tif` raster is generated when the input contains a time dimension and the count export completes successfully.

### RGB and NDVI Products

**RGB** uses the red, green and blue bands in the expected order and retains floating-point reflectance values in the exported raster.

**NDVI** is calculated using:

\[
NDVI = \frac{NIR - Red}{NIR + Red}
\]

The current `calculate_ndvi()` implementation expects already-scaled surface reflectance by default. It does not apply another scale factor or offset when called with its default parameters. It masks non-positive input reflectance and near-zero denominators and does not clip the resulting NDVI values.

### Previews

The application generates PNG previews for RGB and NDVI products.

It also generates before-masking and after-masking RGB previews using shared display-stretch limits, enabling a more consistent visual comparison of the masking effect.

Preview generation is being optimised by downsampling imagery for display. This must not reduce the spatial resolution of the exported scientific COG products.

### Cloud Optimized GeoTIFF Export

Requested products are exported as Cloud Optimized GeoTIFFs using the project's reusable export module.

The export workflow preserves spatial reference information and defines NoData values. Current RGB and NDVI validation results have passed strict `rio-cogeo` checks, including checks for tiling, CRS, data types, NoData, valid pixels and spatial-grid alignment.

Validation should be rerun against the latest exported files after pipeline changes.

### Metadata and Processing Statistics

The reusable metadata module records information about the processing run, including:

- Input parameters and AOI geometry
- Date range and cloud-cover threshold
- Source scene identifiers and acquisition details
- Composite method and requested product
- Raster CRS, dimensions, bounds and resolution
- Data types, NoData values and valid-pixel statistics
- Output paths and file checksums, where available
- Preview and valid-observation-count raster references

The main processing script also creates a masking comparison report containing the before- and after-masking grid details, preview references, and RGB band statistics.

The masking comparison is intended to be included in `metadata.json` so downstream applications can consume the processing metadata through one manifest. Confirm that the metadata module accepts and serialises the `masking_comparison` argument before considering that integration complete.

## 3. Processing Workflow

The current workflow is:

1. Collect and validate AOI selection and processing inputs.
2. Construct the AOI geometry.
3. Connect to the Earth Search STAC API.
4. Search Sentinel-2 scenes and filter by cloud cover.
5. Select a reference scene and load matching imagery.
6. Inspect source metadata and convert optical bands to surface reflectance.
7. Generate the before-masking RGB preview.
8. Validate SCL alignment and apply the SCL mask.
9. Calculate masking statistics and export the valid-observation-count raster.
10. Generate the masked temporal composite.
11. Generate the after-masking preview and compare spatial grids and band statistics.
12. Calculate the requested RGB or NDVI product.
13. Export the product as a COG.
14. Generate the product preview and output-pixel quality report.
15. Generate and save processing metadata.

The current interactive interface requests one product per run: RGB or NDVI.

## 4. Project Structure

The main components are organised as follows:

```text
eo-platform/
├── notebooks/
│   └── aoi_test.py
├── src/
│   └── eo/
│       ├── aoi.py
│       ├── stac.py
│       ├── sentinel2.py
│       ├── composites.py
│       ├── masking.py
│       ├── indices.py
│       ├── processor.py
│       ├── export.py
│       └── metadata.py
├── tests/
│   └── validate_outputs.py
├── outputs/
│   ├── rgb.tif
│   ├── ndvi.tif
│   ├── rgb_preview.png
│   ├── ndvi_preview.png
│   ├── rgb_preview_before_masking.png
│   ├── rgb_preview_after_masking.png
│   ├── valid_observation_count.tif
│   ├── masking_comparison_stats.json
│   └── metadata.json
├── requirements.txt
└── README.md
```

Output files depend on the selected processing product and whether the current run completes successfully. The listing above describes the intended output layout; older files in `outputs/` may remain from previous runs.

## 5. Main Modules

| Module | Responsibility |
|---|---|
| `aoi.py` | Administrative AOI lookup and geometry retrieval |
| `stac.py` | STAC catalog connection |
| `sentinel2.py` | Sentinel-2 search, reference-scene selection and data loading |
| `composites.py` | RGB construction, temporal composites and RGB previews |
| `masking.py` | SCL masking, alignment checks and masking statistics |
| `indices.py` | NDVI calculation |
| `processor.py` | Dispatches the requested RGB or NDVI process |
| `export.py` | Raster product and COG export |
| `metadata.py` | Metadata generation and JSON saving |
| `notebooks/aoi_test.py` | Interactive end-to-end processing workflow |
| `tests/validate_outputs.py` | Raster and COG validation |

## 6. Running the Pipeline

Activate the Python virtual environment and run the script from the project root:

```bash
python notebooks/aoi_test.py
```

The script prompts for:

1. Administrative or coordinate-based AOI
2. AOI parameters
3. Processing year and month
4. Composite method (`median` or `mean`)
5. Maximum scene cloud cover (0–100%)
6. Product (`RGB` or `NDVI`)

The script derives the date range from the selected month. Arbitrary start and end dates are not currently requested by this interface.

## 7. Validation

Run the output validator with:

```bash
python tests/validate_outputs.py
```

The validator checks the exported RGB and NDVI rasters for COG compliance and basic raster integrity. It also checks CRS, dimensions, bounds, resolution, grid alignment and AOI coverage where supported by the validation manifest.

A successful validator run confirms the checks reported by the validator for those specific files. It does not, by itself, establish that the reflectance conversion is scientifically correct or that the SCL mask is radiometrically appropriate.

## 8. Performance Considerations

The pipeline uses Xarray, Dask and `odc-stac` for working with cloud-hosted raster data. Large AOIs can require substantial I/O, memory and computation, particularly during temporal compositing, masking statistics, previews and raster export.

Current optimisation priorities include:

- Avoid repeated full-array `.compute()` and `.values` operations.
- Downsample previews without reducing the resolution of scientific output products.
- Calculate raster statistics in blocks or windows where possible.
- Benchmark Dask chunk sizes against available system memory.
- Consider spatial tiling and windowed processing for county-scale and larger AOIs.
- Preserve masking, metadata and export validation when optimising performance.

## 9. Current Development Priorities

The main remaining verification and development tasks are:

1. Confirm the Sentinel-2 source offset convention and test reflectance conversion.
2. Add automated tests for reflectance calculations, NDVI, zero denominators and NaN inputs.
3. Complete the before/after masking statistics and validate `valid_observation_count.tif`.
4. Confirm that the current metadata implementation includes all masking comparison fields.
5. Investigate the outstanding Rasterio georeferencing warning and verify SCL alignment on the actual data grid.
6. Confirm successful RGB and NDVI runs after the preview optimisation changes.
7. Improve memory efficiency and error handling for large AOIs.
8. Make the full processing workflow callable through a reusable function suitable for backend integration.

## 10. Intended Integration

The pipeline is designed to provide a repeatable processing workflow that accepts AOI and processing parameters and returns georeferenced raster products together with previews and metadata.

he intended integration is with the AFROSAT FastAPI backend, which can use the generated COGs and metadata manifest to expose EO processing results to the frontend. A stable callable processing interface and comprehensive input validation are still part of the development roadmap.