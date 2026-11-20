"""Tests for AIRSAT-05 processor metadata and footprint generation."""

import json

import numpy as np
import rasterio
from rasterio.transform import from_origin
from PIL import Image

from eo.airsat.processor import process_airsat05


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
            <ProjectModel>UTM</ProjectModel>
            <ReceiverSettableGain>1</ReceiverSettableGain>
            <ProcessorGainCorrection>1</ProcessorGainCorrection>
            <ElevationPatternCorrection>1</ElevationPatternCorrection>
            <AmuthPatternCorrection>1</AmuthPatternCorrection>
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

    preview_path = output_dir / "previews" / "sar_hh_preview.jpg"
    thumbnail_path = output_dir / "previews" / "sar_hh_thumbnail.jpg"

    with Image.open(preview_path) as preview:
        assert preview.format == "JPEG"
        assert preview.mode == "RGB"
        preview_pixels = np.asarray(preview)
        assert preview_pixels.shape[2] == 3
        assert np.max(preview_pixels[0]) <= 2

    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.mode == "RGB"


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

    assert metadata["source"] == "airsat"
    assert metadata["satellite"] == "AIRSAT-05"
    assert metadata["product_id"] == (
        "AS05_ABC_DEF_006596_20261028235248_L2_TEST"
    )
    assert metadata["acquisition_date"] == "2026-10-28"

    comparison = footprint["footprint_comparison"]

    assert comparison["comparison_method"]
    assert 0 <= comparison["intersection_over_union"] <= 1
    assert 0 <= comparison["symmetric_difference_percent_of_union"] <= 100
    assert comparison["boundary_hausdorff_distance_m"] >= 0
    assert comparison["raster_area_m2"] > 0
    assert comparison["vendor_footprint_area_m2"] > 0

    comparison = footprint["footprint_comparison"]

    assert comparison["comparison_method"]
    assert 0 <= comparison["intersection_over_union"] <= 1
    assert 0 <= comparison["symmetric_difference_percent_of_union"] <= 100
    assert comparison["boundary_hausdorff_distance_m"] >= 0
    assert comparison["raster_area_m2"] > 0
    assert comparison["vendor_footprint_area_m2"] > 0

    resolution = metadata["spatial_resolution"]

    assert resolution["nominal_resolution_xml_value"] == 0.135346
    assert resolution["vendor_confirmed_nominal_spatial_resolution_m"] == 0.5
    assert resolution["output_pixel_spacing_m"]["width"] == 0.5025935
    assert resolution["output_pixel_spacing_m"]["height"] == 0.5025935

    assert "ground_range_resolution_estimated_m" not in resolution
    assert "azimuth_resolution_estimated_m" not in resolution
    assert "resolution_source" not in resolution
    assert "pixel_spacing_source" not in resolution

    assert metadata["georeferencing"]["correction_level"].startswith("GEC")
    assert metadata["georeferencing"]["terrain_correction_applied"] is False

    assert metadata["radiometry"]["units"] == "DN"
    assert metadata["radiometry"]["value_scale"] == "Linear, not decibels (dB)"
    assert metadata["radiometry"]["calibration_constant_xml"] == "0"
    assert metadata["radiometry"]["calibration_constant_unit"] == "dB"
    assert "vendor confirmation" in (
        metadata["radiometry"]["calibration_constant_confirmation"]
    )
    assert metadata["radiometry"]["sigma0_available"] is False
    assert metadata["radiometry"]["gamma0_available"] is False
    assert metadata["radiometry"]["beta0_available"] is False

    georeferencing = metadata["georeferencing"]
    assert georeferencing["product_level"] == "L2"
    assert georeferencing["product_level_description"].startswith("L2 GEC")
    assert georeferencing["xml_project_model"] == "UTM"
    assert georeferencing["output_crs"] == "EPSG:4326"
    assert "EPSG:4326" in georeferencing["output_grid"]

    corrections = metadata["radiometric_corrections"]
    assert corrections["receiver_settable_gain"]["xml_flag_enabled"] is True
    assert corrections["processor_gain_correction"]["xml_flag_enabled"] is True
    assert corrections["range_antenna_pattern_correction"]["xml_flag_enabled"] is True
    assert corrections["azimuth_antenna_pattern_correction"]["xml_flag_enabled"] is True

    footprint_info = metadata["footprint_information"]
    assert footprint_info["used_as_nodata_mask"] is False
    assert footprint_info["boundary_validation"] == (
        "quantitatively_compared"
    )

    zero_values = metadata["scientific_interpretation"]["zero_values"]
    assert "background outside" in zero_values
    assert "no scientific NoData is assigned" in zero_values

    display_values = metadata["scientific_interpretation"]["display_values"]
    assert "RGB JPEG previews" in display_values
    assert "rendered black" in display_values

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
