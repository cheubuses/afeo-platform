
#!/usr/bin/env python3
"""Independently validate RGB and NDVI COG outputs."""

import argparse
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rio_cogeo.cogeo import cog_validate


NODATA_EXPECTED = -9999.0
RGB_RANGE = (-0.11, 1.60)
NDVI_RANGE = (-1.0001, 1.0001)
GRID_TOLERANCE = 1e-6


def check(condition, message, failures):
    """Print a check result and record failures."""
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        failures.append(message)


def inspect_raster(path, expected_bands, value_range, failures):
    """Check COG validity and raster properties."""
    print(f"\n{'=' * 68}\nFILE: {path}\n{'=' * 68}")

    if not path.is_file():
        check(False, f"File exists: {path}", failures)
        return None

    check(path.stat().st_size > 0, "File is not empty", failures)

    # Independent validation against the COG specification.
    try:
        valid_cog, errors, warnings = cog_validate(
            str(path),
            strict=True,
        )
        check(valid_cog, "rio-cogeo strict COG validation", failures)

        for error in errors:
            print(f"    COG error: {error}")
        for warning in warnings:
            print(f"    COG warning: {warning}")
    except Exception as exc:
        check(False, f"rio-cogeo validation completed: {exc}", failures)

    try:
        with rasterio.open(path) as src:
            print(f"  Driver: {src.driver}")
            print(f"  CRS: {src.crs}")
            print(f"  Dimensions: {src.width} x {src.height}")
            print(f"  Bounds: {tuple(src.bounds)}")
            print(f"  Resolution: {src.res}")
            print(f"  Bands: {src.count}")
            print(f"  Data types: {src.dtypes}")
            print(f"  NoData values: {src.nodatavals}")
            print(f"  Tiled: {src.is_tiled}")
            print(f"  Block shapes: {src.block_shapes}")
            overview_info = {
                band: src.overviews(band)
                for band in range(1, src.count + 1)
            }
            print(f"  Overviews by band: {overview_info}")

            check(src.crs is not None, "CRS is defined", failures)
            check(
                src.count == expected_bands,
                f"Expected {expected_bands} band(s); found {src.count}",
                failures,
            )
            check(
                all(dtype == "float32" for dtype in src.dtypes),
                "All bands use float32",
                failures,
            )
            check(
                all(
                    value is not None
                    and np.isclose(value, NODATA_EXPECTED)
                    for value in src.nodatavals
                ),
                f"NoData is {NODATA_EXPECTED} for every band",
                failures,
            )
            check(src.is_tiled, "Raster uses internal tiling", failures)

            # Overviews are needed for larger images; a small raster may
            # validly have none. Record and assess them accordingly.
            has_overviews = all(
                len(src.overviews(band)) > 0
                for band in range(1, src.count + 1)
            )
            overview_required = max(src.width, src.height) > 512
            check(
                has_overviews or not overview_required,
                "Overviews exist where image size requires them"
                if overview_required
                else "Overview check: not required for this small raster",
                failures,
            )

            pixels = src.read(masked=True)
            valid = pixels.compressed()
            finite = np.isfinite(valid)

            check(valid.size > 0, "At least one valid pixel exists", failures)
            check(np.all(finite), "All valid pixels are finite", failures)

            if valid.size and np.all(finite):
                minimum = float(valid.min())
                maximum = float(valid.max())
                print(f"  Valid-value range: {minimum:.8f} to {maximum:.8f}")
                check(
                    minimum >= value_range[0]
                    and maximum <= value_range[1],
                    f"Valid values within {value_range}",
                    failures,
                )

            return {
                "crs": src.crs,
                "width": src.width,
                "height": src.height,
                "bounds": tuple(src.bounds),
                "resolution": tuple(src.res),
                "transform": src.transform,
                "count": src.count,
                "dtypes": src.dtypes,
                "nodata": src.nodatavals,
            }

    except Exception as exc:
        check(False, f"Raster can be opened and inspected: {exc}", failures)
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Validate RGB and NDVI Cloud Optimized GeoTIFF outputs."
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="outputs",
        help="Directory containing rgb.tif and ndvi.tif (default: outputs)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    failures = []

    print(f"Validating outputs in: {output_dir}")

    rgb = inspect_raster(
        output_dir / "rgb.tif",
        expected_bands=3,
        value_range=RGB_RANGE,
        failures=failures,
    )
    ndvi = inspect_raster(
        output_dir / "ndvi.tif",
        expected_bands=1,
        value_range=NDVI_RANGE,
        failures=failures,
    )

    print(f"\n{'=' * 68}\nSPATIAL GRID COMPARISON\n{'=' * 68}")

    if rgb is None or ndvi is None:
        check(False, "Both rasters could be inspected", failures)
    else:
        check(rgb["crs"] == ndvi["crs"], "RGB and NDVI CRS match", failures)
        check(
            (rgb["width"], rgb["height"])
            == (ndvi["width"], ndvi["height"]),
            "RGB and NDVI dimensions match",
            failures,
        )
        check(
            np.allclose(
                rgb["bounds"], ndvi["bounds"], atol=GRID_TOLERANCE, rtol=0
            ),
            "RGB and NDVI bounds match",
            failures,
        )
        check(
            np.allclose(
                rgb["resolution"],
                ndvi["resolution"],
                atol=GRID_TOLERANCE,
                rtol=0,
            ),
            "RGB and NDVI resolution matches",
            failures,
        )
        check(
            np.allclose(
                tuple(rgb["transform"]),
                tuple(ndvi["transform"]),
                atol=GRID_TOLERANCE,
                rtol=0,
            ),
            "RGB and NDVI pixel grids align",
            failures,
        )

    # If the metadata manifest exists, check whether the RGB and NDVI
    # raster bounds cover its WGS84 AOI bounding box.
    manifest_path = output_dir / "metadata.json"
    if manifest_path.is_file():
        import json

        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            aoi = manifest["input"]["aoi_bbox_wgs84"]

            for label, info in (("RGB", rgb), ("NDVI", ndvi)):
                if info is None:
                    continue

                aoi_bounds = transform_bounds(
                    "EPSG:4326",
                    info["crs"],
                    *aoi,
                    densify_pts=21,
                )
                left, bottom, right, top = info["bounds"]
                aoi_left, aoi_bottom, aoi_right, aoi_top = aoi_bounds
                tol_x, tol_y = abs(info["resolution"][0]), abs(
                    info["resolution"][1]
                )

                covers = (
                    left <= aoi_left + tol_x
                    and bottom <= aoi_bottom + tol_y
                    and right >= aoi_right - tol_x
                    and top >= aoi_top - tol_y
                )
                check(
                    covers,
                    f"{label} raster covers manifest AOI within one pixel",
                    failures,
                )
        except Exception as exc:
            check(False, f"AOI coverage check completed: {exc}", failures)
    else:
        print("\n[INFO] No metadata.json found; AOI coverage not checked.")

    print(f"\n{'=' * 68}")
    if failures:
        print(f"RESULT: FAIL ({len(failures)} failed check(s))")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("RESULT: PASS (all required checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
