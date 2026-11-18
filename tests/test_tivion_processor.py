"""Tests for AIRSAT-05 processor metadata and footprint generation."""

import json

import numpy as np
import rasterio
from rasterio.transform import from_origin
from PIL import Image

from eo.tivion.processor import process_airsat05


def test_airsat05_metadata_and_two_part_footprint(tmp_path):
    raster_path = tmp_path / "source.tif"
    xml_path = tmp_path / "source.xml"
    output_dir = tmp_path / "output"

    data = np.arange(1, 101, dtype=np.uint16).reshape(10, 10)
    data[0, :] = 0  # Simulate background pixels for transparency testing.

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=from_origin(36.9, -1.26, 0.0001, 0.0001),
    ) as dst:
        dst.write(data, 1)

    xml_path.write_text(
        """<?xml version="1.0"?>
        <product>
            <Productid>AS05_ABC_DEF_006596_20261028235248_L2_TEST</Productid>
            <productLevel>L2</productLevel>
            <NominalResolution>0.135346</NominalResolution>
            <Widthspace>5.025935e-01</Widthspace>
            <Heightspace>5.025935e-01</Heightspace>
            <CalibrationConst>0</CalibrationConst>
            <RadiometricModel>0</RadiometricModel>
            <corner>
                <topLeft>
                    <Latitude>-1.279723</Latitude>
                    <Longitude>36.984885</Longitude>
                </topLeft>
                <topRight>
                    <Latitude>-1.266055</Latitude>
                    <Longitude>36.910735</Longitude>
                </topRight>
                <bottomLeft>
                    <Latitude>-1.373979</Latitude>
                    <Longitude>36.966536</Longitude>
                </bottomLeft>
                <bottomRight>
                    <Latitude>-1.357920</Latitude>
                    <Longitude>36.892746</Longitude>
                </bottomRight>
            </corner>
        </product>
        """,
        encoding="utf-8",
    )

    result = process_airsat05(
        input_file=raster_path,
        metadata_file=xml_path,
        output_dir=output_dir,
    )

    assert result["status"] == "completed"
    assert result["validation"]["pixel_values_identical"] is True

    preview_path = output_dir / "previews" / "sar_hh_preview.png"
    thumbnail_path = output_dir / "previews" / "sar_hh_thumbnail.png"

    with Image.open(preview_path) as preview:
        assert preview.mode == "RGBA"
        alpha = np.asarray(preview.getchannel("A"))
        assert np.any(alpha == 0)
        assert np.any(alpha == 255)

    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.mode == "RGBA"

    metadata = json.loads(
        (output_dir / "metadata" / "metadata.json").read_text()
    )

    footprint = json.loads(
        (output_dir / "metadata" / "footprint.geojson").read_text()
    )

    assert len(footprint["features"]) == 2

    features = {
        feature["properties"]["geometry_role"]: feature
        for feature in footprint["features"]
    }

    assert set(features) == {
        "raster_extent",
        "vendor_scene_footprint",
    }

    vendor_ring = features["vendor_scene_footprint"]["geometry"][
        "coordinates"
    ][0]

    assert vendor_ring == [
        [36.984885, -1.279723],
        [36.910735, -1.266055],
        [36.892746, -1.357920],
        [36.966536, -1.373979],
        [36.984885, -1.279723],
    ]

    resolution = metadata["spatial_resolution"]

    assert resolution["nominal_resolution_xml_value"] == 0.135346
    assert resolution["vendor_confirmed_nominal_spatial_resolution_m"] == 0.5
    assert resolution["ground_range_resolution_estimated_m"] == 0.45
    assert resolution["azimuth_resolution_estimated_m"] == 0.62
    assert resolution["output_pixel_spacing_m"]["width"] == 0.5025935
    assert resolution["output_pixel_spacing_m"]["height"] == 0.5025935

    assert metadata["georeferencing"]["correction_level"].startswith("GEC")
    assert metadata["georeferencing"]["terrain_correction_applied"] is False

    assert metadata["radiometry"]["units"] == "DN"
    assert metadata["radiometry"]["sigma0_available"] is False

    assert metadata["footprint_information"]["used_as_nodata_mask"] is False

    assert metadata["scientific_interpretation"]["zero_values"].find(
        "NoData mask is applied"
    ) >= 0

    with rasterio.open(raster_path) as original:
        original_pixels = original.read(1)

    with rasterio.open(output_dir / "cog" / "sar_hh_cog.tif") as cog:
        cog_pixels = cog.read(1)

    np.testing.assert_array_equal(original_pixels, cog_pixels)

    map_cog_path = output_dir / "cog" / "sar_hh_map_cog.tif"

    with rasterio.open(map_cog_path) as map_cog:
        map_pixels = map_cog.read(1)
        map_mask = map_cog.read_masks(1)

        assert map_cog.count == 1
        assert map_cog.crs.to_epsg() == 4326
        np.testing.assert_array_equal(original_pixels, map_pixels)

        assert np.all(map_mask[original_pixels == 0] == 0)
        assert np.all(map_mask[original_pixels != 0] == 255)

    assert metadata["map_cog"]["pixel_values_identical"] is True
    assert metadata["map_cog"]["internal_mask_validated"] is True
    assert metadata["asset_paths"]["sar_hh_map_cog"] == (
        "cog/sar_hh_map_cog.tif"
    )

    manifest = json.loads(
        (output_dir / "manifest.json").read_text()
    )

    assert manifest["assets"]["sar_hh_map_cog"] == (
        "cog/sar_hh_map_cog.tif"
    )
