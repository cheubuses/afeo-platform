# ============================================================
# EXPORT EO PRODUCTS AS CLOUD OPTIMIZED GEOTIFFS (COGs)
# ============================================================

from pathlib import Path


def export_cog(data, output_path):
    """
    Export an EO product as a Cloud Optimized GeoTIFF (COG).

    Parameters
    ----------
    data : xarray.DataArray or xarray.Dataset
        The processed EO product, such as NDVI or RGB.
    output_path : str or pathlib.Path
        Destination path for the output COG.

    Returns
    -------
    str
        Path to the exported COG.
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the COG.
    data.rio.to_raster(
        output_path,
        driver="COG"
    )

    return str(output_path)