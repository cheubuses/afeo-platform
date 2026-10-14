
from pathlib import Path

import numpy as np


NODATA_VALUE = -9999.0


def export_cog(
    data,
    output_path,
    nodata=NODATA_VALUE,
    compression="ZSTD",
):
    """
    Export an Xarray DataArray or Dataset variable as a compressed COG.

    Uses Float32 and lossless compression by default.
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not hasattr(data, "rio"):
        raise TypeError(
            "Input data must have an rioxarray .rio accessor."
        )

    # Reduce storage size compared with Float64.
    output = data.astype("float32")

    # Replace invalid floating-point pixels with explicit NoData.
    output = output.where(
        np.isfinite(output),
        other=np.float32(nodata),
    )

    output = output.rio.write_nodata(
        nodata,
        encoded=False,
        inplace=False,
    )

    # Export as a compressed Cloud Optimized GeoTIFF.
    output.rio.to_raster(
        output_path,
        driver="COG",
        dtype="float32",
        nodata=nodata,
        compress=compression,
        predictor="FLOATING_POINT",
        blocksize=512,
        BIGTIFF="IF_SAFER",
    )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(
            f"COG export failed: {output_path}"
        )

    size_mb = output_path.stat().st_size / (1024 ** 2)

    print(f"Exported COG: {output_path}")
    print(f"File size: {size_mb:.2f} MB")
    print(f"Compression: {compression}")
    print("Data type: float32")
    print(f"NoData: {nodata}")

    return str(output_path)
