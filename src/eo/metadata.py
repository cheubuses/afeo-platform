
import json
from datetime import datetime, timezone
from pathlib import Path


def create_metadata(best_item, data, process, output_path):
    """Create metadata for the selected EO product."""

    process = process.upper()
    output_path = Path(output_path)

    metadata = {
        "product": {
            "name": process,
            "format": "COG",
            "filename": output_path.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "source": {
            "scene_id": best_item.id,
            "collection": best_item.collection_id,
            "datetime": best_item.properties.get("datetime"),
            "cloud_cover": best_item.properties.get("eo:cloud_cover"),
            "platform": best_item.properties.get("platform"),
            "constellation": best_item.properties.get("constellation"),
            "processing_level": best_item.properties.get(
                "processing:level"
            ),
        },
        "temporal_composite": {
            "frequency": "monthly",
            "year": year,
            "month": month,
            "method": composite_method,
            "start_date": start_date,
            "end_date": end_date
},
        "spatial": {
            "epsg": best_item.properties.get("proj:epsg"),
            "crs": str(data.rio.crs),
            "bbox": best_item.bbox,
            "geometry": best_item.geometry,
            "width": int(data.sizes["x"]),
            "height": int(data.sizes["y"]),
        },
        "processing": {
            "product_type": process,
            "ndvi": process == "NDVI",
            "method": (
                "RGB composite"
                if process == "RGB"
                else "NDVI = (NIR - Red) / (NIR + Red)"
                if process == "NDVI"
                else "Unknown processing method"
            ),
        },
        "outputs": {
    "cog": output_file.name,
    "rgb_preview": (
        "rgb_preview.png" if process == "RGB" else None
    ),
    "ndvi_preview": (
        "ndvi_preview.png" if process == "NDVI" else None
    )
}
    }

    return metadata


def save_metadata(metadata, output_path="metadata.json"):
    """Save the metadata manifest as a JSON file."""

    output_path = Path(output_path)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=4, default=str)

    print(f"Metadata saved as {output_path}")

    return output_path

