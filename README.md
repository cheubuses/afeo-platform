# Cloud-Native Earth Observation Processing Platform

# 1. Project Overview

This project implements a modular, Python-based Earth Observation (EO) processing workflow for searching, loading, processing, and exporting Sentinel-2 satellite imagery using cloud-native geospatial technologies, namely: STAC, XArray, Dask, and Cloud Optimized GeoTIFFs (COGs).

The platform allows useer to select their area of interest and select an EO processing operation such as **RGB, NDVI, NDWI, NDBI, EVI, LST, flood mapping, and other analyses** through a simple interface.


# Workflow
The workflow allows a user to specify a geographic location, processing radius, satellite image date range, and desired processing product. The system searches the Earth Search SpatioTemporal Asset Catalog (STAC), identifies the Sentinel-2 scene with the lowest cloud cover ( We will change this later, to composite ( Mean/median)), loads the required satellite bands, and generates the requested EO product.

The current implementation supports:

* **RGB composites** for visual interpretation.
* **Normalized Difference Vegetation Index (NDVI)** for vegetation analysis.
* **Cloud Optimized GeoTIFF (COG)** output.
* **JSON metadata** describing user inputs, search criteria, selected imagery, spatial information, and output files.

The project is designed as a reusable processing engine for integration into a FastAPI backend and a web-based EO platform.

## 2. Project Objectives

1. Search for Sentinel-2 Level-2A imagery using a STAC API.
2. Allow users to define an area of interest (AOI) using coordinates and a processing radius.
3. Allow users to specify the start and end dates for satellite image searches.
4. Filter imagery using a cloud-cover threshold.
5. Rank available scenes and select the scene with the lowest reported cloud cover.
6. Load Sentinel-2 spectral bands into an XArray dataset.
7. Use Dask-compatible chunking to support lazy processing of raster data.
8. Generate RGB composites and NDVI products.
9. Export the selected product as a Cloud Optimized GeoTIFF.
10. Generate structured JSON metadata for downstream applications.

## 3. Technologies and Libraries

| Technology    | Purpose                                            |
| ------------- | -------------------------------------------------- |
| Python        | Main processing language                           |
| PySTAC Client | Connect to and search the STAC catalog             |
| Earth Search  | Sentinel-2 imagery discovery                       |
| `odc-stac`    | Load cloud-hosted STAC assets into XArray          |
| XArray        | Multidimensional raster data management            |
| Dask          | Lazy evaluation and chunked processing             |
| Rioxarray     | Geospatial raster metadata and raster export       |
| Rasterio/GDAL | Geospatial raster interoperability and COG support |
| NumPy         | Numerical array operations                         |
| Matplotlib    | Visualization and preview generation               |
| Shapely       | AOI geometry creation                              |
| JSON          | Processing metadata serialization                  |

## 4. Project Structure

The project separates reusable EO processing functions from the main test script.

```text
eo-platform/
├── README.md
├── notebooks/
│   └── aoi_test.py
└── src/
    └── eo/
        ├── __init__.py
        ├── stac.py
        ├── sentinel2.py
        ├── indices.py
        ├── composites.py
        ├── export.py
        └── processor.py
```

### Module descriptions

**`stac.py`**

Connects to the Earth Search STAC API.
**`sentinel2.py`**

Implements Sentinel-2 scene discovery, selection of the lowest-cloud-cover scene, and loading of the required spectral bands.

**`indices.py`**

Contains spectral index calculations, currently including NDVI.

**`composites.py`**

Creates an RGB band stack from the loaded red, green, and blue bands.

**`export.py`**

Exports processed XArray data as Cloud Optimized GeoTIFF files.

**`processor.py`**

Acts as the processing dispatcher. It accepts a requested product and calls the corresponding RGB or NDVI processing function.

**`notebooks/aoi_test.py`**

Runs the complete workflow interactively, from user inputs through satellite discovery, processing, export, and metadata generation.

## 5. Processing Workflow

The current pipeline follows these steps:

1. **Initialize the environment:** Import the required Python libraries and make the local `src` package available to Python.
2. **Configure cloud access:** Enable unsigned access to publicly accessible AWS S3 assets.
3. **Collect user inputs:** Request latitude, longitude, processing radius, start date, and end date.
4. **Validate dates:** Check that the dates use the ISO format `YYYY-MM-DD` and that the start date is not later than the end date.
5. **Create the AOI:** Convert the coordinates and radius into a bounding-box geometry.
6. **Connect to STAC:** Open the Earth Search catalog.
7. **Search imagery:** Query the `sentinel-2-l2a` collection using the AOI, date range, and cloud-cover threshold.
8. **Select a scene:** Choose the returned scene with the lowest reported cloud cover.
9. **Load spectral bands:** Load red, green, blue, and near-infrared bands into an XArray dataset with Dask chunking.
10. **Select a processing product:** Ask the user to choose RGB or NDVI.
11. **Process the data:** Create an RGB band stack or calculate NDVI.
12. **Export the product:** Save the selected output as a COG.
13. **Generate metadata:** Write processing information to `metadata.json`.

## 6. User Inputs

The workflow accepts the following inputs:

| Input        | Data type | Description                                 
 ------------ | --------- | ------------------------------------------- 
 `latitude`   | Float     | Latitude of the AOI centre                  
`longitude`  | Float     | Longitude of the AOI centre                 
`radius_km`  | Float     | Approximate radius of the AOI in kilometres 
`start_date` | String    | Beginning of the satellite search period    
 `end_date`   | String    | End of the satellite search period          
 `process`    | String    | Requested product: `RGB` or `NDVI`          

# Inputs Example

Enter latitude: -0.3035
Enter longitude: 36.0170
Enter processing radius in km: 10
Enter start date (YYYY-MM-DD): 2026-06-01
Enter end date (YYYY-MM-DD): 2026-09-30
Select processing option (RGB or NDVI): NDVI




The coordinates above are an example. Users can supply coordinates for their own areas of interest.

## 7. Satellite Data Discovery and Selection

The pipeline connects to the following STAC endpoint:

`https://earth-search.aws.element84.com/v1`

It searches the Sentinel-2 Level-2A collection:

`sentinel-2-l2a`

The search uses:

* AOI geometry.
* User-defined start and end dates.
* A maximum cloud-cover threshold of 30%.

The scene-selection function ranks the returned scenes by their reported `eo:cloud_cover` property and selects the scene with the lowest value.

**Current limitation:** This is a single-scene selection workflow. It does not yet construct a temporal composite from multiple acquisitions. Scene-level cloud cover also does not guarantee that every pixel within the AOI is cloud-free.

## 8. Supported Processing Products

### RGB composite

The RGB product uses these Sentinel-2 spectral bands:

    Display channel  Spectral band 
---------------  ------------- 
    Red              B04           
    Green            B03           
    Blue             B02           

The bands are loaded through the named variables `red`, `green`, and `blue`, then combined into an XArray array with a `band` dimension.

Output:

`rgb.tif`

### Normalized Difference Vegetation Index (NDVI)

NDVI is calculated using the red and near-infrared bands:

$$
NDVI = \frac{NIR - Red}{NIR + Red}
$$

For Sentinel-2, these correspond to B08 (near-infrared) and B04 (red).

NDVI is commonly used to assess vegetation greenness and relative vegetation condition.

Output:

`ndvi.tif`

**Note:** Explicit handling of zero denominators, invalid pixels, and cloud/shadow masks should be added to the index-processing workflow before using the product for operational analysis.

## 9. Output Products

The output depends on the user's processing selection.

| Output          | Description                                                                                |
| --------------- | ------------------------------------------------------------------------------------------ |
| `rgb.tif`       | RGB composite exported as a COG                                                            |
| `ndvi.tif`      | NDVI raster exported as a COG                                                              |
| `metadata.json` | JSON record of the inputs, search, selected scene, spatial details, and output information |

The script exports the requested product rather than both products in the same run.

A preview image (`rgb_preview.png`) is a planned output, but RGB preview generation needs to be restored and tested in the current script before it can be considered a working output.

## 10. Metadata Structure

The metadata file is intended to describe the processing request and its results.

Its principal sections are:

* `input`: coordinates and processing radius.
* `stac_search`: catalog, collection, date range, and cloud-cover threshold.
* `scene`: scene identifier, acquisition datetime, cloud cover, platform, constellation, and processing level.
* `spatial`: CRS, EPSG code, raster width and height, and scene bounding box.
* `processing`: selected processing product and processing configuration.
* `outputs`: generated COG filename and any preview filename.

The metadata is designed to support downstream API responses, result tracking, and integration with a web application.

**Implementation note:** The current metadata code marks both `rgb` and `ndvi` as `true`, even though only one product is selected per run. The metadata should be updated to record the actual requested product. The spatial metadata should also be checked against the exported raster's CRS, dimensions, and bounds.

## 11. Cloud-Native Processing Approach

The workflow uses STAC-based discovery and reads cloud-hosted satellite assets without requiring users to download an entire scene manually.

XArray provides structured access to raster bands, while Dask chunking supports lazy evaluation and enables larger processing workloads to be handled in manageable chunks.

The COG format supports efficient access to raster data through HTTP range requests when served by a compatible storage or web server.

These design choices provide a foundation for scaling the workflow and integrating it into a cloud-hosted EO service. They do not, by themselves, guarantee that every processing step is lazy or that the complete workflow runs on distributed infrastructure.

##  12. Running the Workflow

Activate the project's Python virtual environment and navigate to the project root
1. Activate the workflow:
cd ~/eo-platform
2. Run the workflow:
python notebooks/aoi_test.py


Provide the requested coordinates, radius, date range, and processing option when prompted.

The workflow will search for imagery, select a scene, process the requested product, export the COG, and write the metadata file if all processing steps complete successfully.


# 13. Current Development Status

### Implemented

* Earth Search STAC connection.
* Sentinel-2 Level-2A scene search.
* User-specified AOI and date range.
* Date-format and date-order validation.
* Lowest-cloud-cover scene selection.
* Loading of red, green, blue, and near-infrared bands.
* RGB band-stack creation.
* NDVI calculation.
* Processing dispatch through `processor.py`.
* COG export function.
* JSON metadata generation.

### Next development priorities

1. Correct the RGB preview and conditional output logic.
2. Make metadata accurately reflect the selected product and actual outputs.
3. Add explicit cloud and shadow masking.
4. Handle invalid values and zero denominators in NDVI calculations.
5. Add RGB and NDVI processing for multiple scenes and temporal composites.
6. Support additional indices, including NDWI and NDBI.
7. Export consistent spatial metadata, raster statistics, and quality information.
8. Add automated tests for the individual modules.
9. Define a stable JSON response schema for the future FastAPI backend.
10. Integrate the processing engine with the EO platform frontend.


## 14. Project Vision

The long-term objective is to develop a reusable and extensible EO processing engine that makes satellite-derived information easier to request and consume through a web interface.

The modular design will support new satellite data sources, additional spectral indices, multi-scene compositing, scalable cloud processing, and standardized outputs for geospatial applications and decision-support systems.
