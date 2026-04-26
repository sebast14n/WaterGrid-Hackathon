"""Project configuration: AOI, date ranges, output settings.
No GEE dependency — uses Planetary Computer / STAC via geoai-py.
"""
import json
from pathlib import Path

# Area of interest bounding box: [west, south, east, north]
# Derived from geo.json (ROSCI0434 — Siretul Mijlociu, Romania)
AOI_BBOX = [26.94418657349121, 46.45436033015628, 27.00760413540865, 46.65837577224148]


def get_aoi_geojson() -> dict:
    """Return AOI as a GeoJSON FeatureCollection with Z-coords stripped."""
    geo_path = Path(__file__).parent / "geo.json"
    with open(geo_path) as f:
        data = json.load(f)
    for feature in data.get("features", []):
        geom = feature.get("geometry", {})
        if geom.get("type") == "Polygon":
            geom["coordinates"] = [
                [[lon, lat] for lon, lat, *_ in ring]
                for ring in geom["coordinates"]
            ]
    return data


# Years to process
YEARS = [2022, 2023, 2024]

# Growing season window (May-Sep) for cloud-free imagery
SEASON_START = "05-01"
SEASON_END   = "09-30"


def time_range_for_year(year: int) -> str:
    """Return STAC time_range string, e.g. '2023-05-01/2023-09-30'."""
    return f"{year}-{SEASON_START}/{year}-{SEASON_END}"


# Planetary Computer Sentinel-2 collection
S2_COLLECTION = "sentinel-2-l2a"
S2_CLOUD_MAX  = 20          # max cloud cover %
S2_ALL_BANDS  = ["B02", "B03", "B04", "B08", "B8A", "B11", "B12", "SCL"]

# Output directories
OUTPUT_DIR = Path(__file__).parent / "outputs"
TIFF_DIR   = OUTPUT_DIR / "tiffs"
CHANGE_DIR = OUTPUT_DIR / "change"

for d in (OUTPUT_DIR, TIFF_DIR, CHANGE_DIR):
    d.mkdir(parents=True, exist_ok=True)
