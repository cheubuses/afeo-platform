
import os
import numpy as np
import rasterio

path = "outputs/rgb.tif"

with rasterio.open(path) as src:

    print("\n" + "=" * 55)
    print("          RGB RASTER INFORMATION")
    print("=" * 55)

    print("File:", path)
    print("Driver:", src.driver)
    print("Dimensions:", src.width, "x", src.height)
    print("Number of bands:", src.count)
    print("Data types:", src.dtypes)
    print("NoData:", src.nodata)
    print("Scales:", src.scales)
    print("Offsets:", src.offsets)
    print("Resolution:", src.res)
    print("CRS:", src.crs)
    print("Bounds:", src.bounds)
    print("Tiled:", src.is_tiled)
    print("Block shapes:", src.block_shapes)
    print("Overviews:", [
        src.overviews(i) for i in range(1, src.count + 1)
    ])
    print("Compression:", src.compression)
    print("File size (MB):", round(
        os.path.getsize(path) / (1024 * 1024), 3
    ))

    band_names = ["Red", "Green", "Blue"]

    for band_index in range(1, src.count + 1):

        band = src.read(band_index, masked=True)
        valid = band.compressed()

        print(f"\n{band_names[band_index - 1] if band_index <= 3 else 'Band ' + str(band_index)} (Band {band_index})")
        print("  Valid pixels:", int(valid.size))
        print("  NoData/masked pixels:", int(band.size - valid.size))

        if valid.size:
            print("  Valid min:", float(valid.min()))
            print("  Valid max:", float(valid.max()))
            print("  Valid mean:", float(valid.mean()))
            print("  Valid standard deviation:", float(valid.std()))
        else:
            print("  No valid pixels found")

    print("\n" + "=" * 55)
    print("RGB raster inspection complete.")
