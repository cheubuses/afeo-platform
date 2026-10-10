def export_cog(
    data,
    output_path
):
    """
    Export an XArray DataArray as a
    Cloud Optimized GeoTIFF (COG).
    """

    data.rio.to_raster(
        output_path,
        driver="COG"
    )

    return output_path