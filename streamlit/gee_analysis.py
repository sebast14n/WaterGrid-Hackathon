"""
Google Earth Engine — Illegal Land Change Detection
====================================================
Area of Interest : ROSCI0434 — Siretul Mijlociu, Romania
Sensor           : Sentinel-2 Level-2A (COPERNICUS/S2_SR_HARMONIZED)
Cloud masking    : SCL band  +  s2cloudless probability layer
Indices          : NDVI · BSI · Plastic/Waste Index (PI)
Method           : Bi-temporal change detection (Before vs After)

Processing pipeline
-------------------
  1. Load S2 L2A collection  →  filter by ROI + date + cloud %
  2. Join S2_CLOUD_PROBABILITY  →  mask pixels where prob > threshold
  3. Also mask SCL classes  (cloud shadow / cloud / cirrus / snow)
  4. Reduce to seasonal median composite  (same season both years)
  5. Compute BSI, NDVI, Plastic Index for each composite
  6. Subtract Before from After  →  change maps
  7. Threshold  →  binary anomaly masks
  8. Visualise with geemap
"""
from __future__ import annotations

import ee
import geemap

# ── 1. Authenticate & initialise ─────────────────────────────────────────────
# Run `earthengine authenticate` once in your terminal before using this script.
try:
    ee.Initialize(project="project-ult-60cf8")
except Exception:
    ee.Authenticate()
    ee.Initialize(project="project-ult-60cf8")


# ── 2. Region of Interest ─────────────────────────────────────────────────────
# ROSCI0434 — Siretul Mijlociu bounding box derived from geo.json
ROI = ee.Geometry.BBox(
    west  = 26.94418657349121,
    south = 46.45436033015628,
    east  = 27.00760413540865,
    north = 46.65837577224148,
)

# ── 3. Parameters ─────────────────────────────────────────────────────────────
YEAR_BEFORE   = 2023
YEAR_AFTER    = 2024
SEASON_START  = "05-01"          # compare same season to avoid phenology noise
SEASON_END    = "09-30"
CLOUD_MAX_PCT = 10               # metadata-level pre-filter
CLOUD_PROB_THRESH = 40           # s2cloudless pixel-level threshold (0–100)

# Thresholds for change detection anomaly masks
BSI_CHANGE_THRESH  = 0.05        # ΔBSI > threshold  →  new bare soil / excavation
NDVI_LOSS_THRESH   = -0.10       # ΔNDVI < threshold →  vegetation loss
PI_ANOMALY_THRESH  = 0.15        # PI > threshold    →  potential plastic/waste


# ── 4. Cloud masking ──────────────────────────────────────────────────────────

def mask_s2_scl(image: ee.Image) -> ee.Image:
    """Pixel-level cloud/shadow mask using the SCL band (Sentinel-2 L2A).

    SCL classes removed:
      0  No data       1  Saturated/defective
      3  Cloud shadow  8  Cloud (medium prob)
      9  Cloud (high prob)   10  Thin cirrus   11  Snow/ice
    """
    scl = image.select("SCL")
    bad = (scl.eq(0).Or(scl.eq(1))
              .Or(scl.eq(3))
              .Or(scl.eq(8)).Or(scl.eq(9))
              .Or(scl.eq(10)).Or(scl.eq(11)))
    return image.updateMask(bad.Not())


def add_cloud_prob_mask(s2_img: ee.Image, cloud_prob_img: ee.Image) -> ee.Image:
    """Add s2cloudless probability band and mask pixels above threshold."""
    prob = cloud_prob_img.select("probability")
    is_cloud = prob.gt(CLOUD_PROB_THRESH)
    return s2_img.updateMask(is_cloud.Not())


def build_composite(year: int) -> ee.Image:
    """Build a cloud-free seasonal median composite for a given year.

    Steps:
      a) Filter S2 L2A by ROI, date range, and metadata cloud %.
      b) Join the s2cloudless probability collection to every image.
      c) Apply SCL pixel mask.
      d) Apply s2cloudless probability mask.
      e) Reduce to median.
    """
    start = f"{year}-{SEASON_START}"
    end   = f"{year}-{SEASON_END}"

    # --- a) Base S2 L2A collection ---
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(ROI)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_MAX_PCT))
    )

    # --- b) s2cloudless probability collection ---
    s2_cloud_prob = (
        ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY")
        .filterBounds(ROI)
        .filterDate(start, end)
    )

    # Join cloud probability to each S2 image by system:index
    join = ee.Join.saveFirst("cloud_prob")
    condition = ee.Filter.equals(
        leftField="system:index", rightField="system:index"
    )
    joined = ee.ImageCollection(join.apply(s2, s2_cloud_prob, condition))

    # --- c+d) Apply both masks ---
    def apply_masks(img: ee.Image) -> ee.Image:
        cloud_prob_img = ee.Image(img.get("cloud_prob"))
        img = mask_s2_scl(img)
        img = add_cloud_prob_mask(img, cloud_prob_img)
        return img

    masked = joined.map(apply_masks)

    # --- e) Seasonal median composite clipped to ROI ---
    return masked.median().clip(ROI)


# ── 5. Build composites ───────────────────────────────────────────────────────
print(f"Building composite: {YEAR_BEFORE} ({SEASON_START} → {SEASON_END}) ...")
composite_before = build_composite(YEAR_BEFORE)

print(f"Building composite: {YEAR_AFTER}  ({SEASON_START} → {SEASON_END}) ...")
composite_after  = build_composite(YEAR_AFTER)


# ── 6. Spectral indices ───────────────────────────────────────────────────────

def compute_bsi(img: ee.Image) -> ee.Image:
    """Bare Soil Index — highlights excavations and bare earth.

    BSI = ((SWIR1 + Red) - (NIR + Blue)) / ((SWIR1 + Red) + (NIR + Blue))
    High BSI (+) = bare soil / rock / mine tailings
    Low  BSI (-) = dense vegetation / water
    """
    blue  = img.select("B2")
    red   = img.select("B4")
    nir   = img.select("B8")
    swir1 = img.select("B11")
    num = swir1.add(red).subtract(nir.add(blue))
    den = swir1.add(red).add(nir.add(blue))
    return num.divide(den).rename("BSI")


def compute_ndvi(img: ee.Image) -> ee.Image:
    """NDVI — vegetation cover. Loss of NDVI = forest clearing / disturbance."""
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")


def compute_plastic_index(img: ee.Image) -> ee.Image:
    """Plastic / Waste Index (PI).

    PI = NIR / (NIR + SWIR2)
    Plastic and synthetic materials reflect NIR strongly relative to SWIR2.
    High PI in a non-water, non-vegetation context = potential plastic/waste.
    Reference: Topouzelis et al. (2019) — adapted for land-based waste.
    """
    nir   = img.select("B8")
    swir2 = img.select("B12")
    return nir.divide(nir.add(swir2)).rename("PI")


# Compute indices for both periods
bsi_before  = compute_bsi(composite_before)
bsi_after   = compute_bsi(composite_after)

ndvi_before = compute_ndvi(composite_before)
ndvi_after  = compute_ndvi(composite_after)

pi_before   = compute_plastic_index(composite_before)
pi_after    = compute_plastic_index(composite_after)


# ── 7. Bi-temporal change detection ──────────────────────────────────────────

# ΔBSI: positive = new bare soil (excavation / gravel pit / land clearing)
delta_bsi  = bsi_after.subtract(bsi_before).rename("delta_BSI")

# ΔNDVI: negative = vegetation loss (forest clearing / burning)
delta_ndvi = ndvi_after.subtract(ndvi_before).rename("delta_NDVI")

# ΔPI: positive = new spectral anomaly consistent with waste/plastic
delta_pi   = pi_after.subtract(pi_before).rename("delta_PI")


# ── 8. Threshold → binary anomaly masks ──────────────────────────────────────

# New bare soil / mine excavation
excavation_mask = delta_bsi.gt(BSI_CHANGE_THRESH).rename("excavation")

# Vegetation loss (combined: NDVI drop AND some BSI increase)
veg_loss_mask = (
    delta_ndvi.lt(NDVI_LOSS_THRESH)
    .And(delta_bsi.gt(0))
    .rename("veg_loss")
)

# Potential waste / plastic anomaly (new PI spike, not water)
water_mask = composite_after.normalizedDifference(["B3", "B8"]).gt(0.1)  # NDWI > 0.1
waste_mask = (
    delta_pi.gt(PI_ANOMALY_THRESH)
    .And(water_mask.Not())                   # exclude open water
    .rename("waste_anomaly")
)


# ── 9. Visualisation with folium (GEE tile URLs) ─────────────────────────────
# geemap.Map requires a Jupyter kernel — use plain folium instead so this
# script also works from the terminal and inside Streamlit.

import folium

# Vis params
rgb_vis        = {"bands": "B4,B3,B2", "min": "0",    "max": "3000",  "gamma": "1.4"}
delta_bsi_vis  = {"min": "-0.2", "max": "0.2",  "palette": "#1a9641,#ffffbf,#d7191c"}
delta_ndvi_vis = {"min": "-0.4", "max": "0.4",  "palette": "#d7191c,#ffffbf,#1a9641"}
pi_vis         = {"min": "0.3",  "max": "0.7",  "palette": "#ffffff,#ff7f00,#8b0000"}


def _tile(img: ee.Image, vis: dict) -> str:
    """Return a GEE XYZ tile URL for use in folium TileLayer."""
    map_id = ee.data.getMapId({**vis, "image": img})
    return map_id["tile_fetcher"].url_format


Map = folium.Map(
    location=[46.556, 26.976], zoom_start=11,
    tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    attr="Google Satellite",
)

layers = [
    (composite_before, rgb_vis,        f"RGB Before {YEAR_BEFORE}", False),
    (composite_after,  rgb_vis,        f"RGB After {YEAR_AFTER}",   True),
    (delta_bsi,        delta_bsi_vis,  "ΔBSI (bare soil change)",   True),
    (delta_ndvi,       delta_ndvi_vis, "ΔNDVI (vegetation change)", False),
    (pi_after,         pi_vis,         f"Plastic Index {YEAR_AFTER}", False),
    (delta_pi,         pi_vis,         "ΔPlastic Index",             False),
    (excavation_mask.selfMask(), {"palette": "#FF0000"}, "🔴 Excavation / Mine",     True),
    (veg_loss_mask.selfMask(),   {"palette": "#FF8C00"}, "🟠 Vegetation Loss",        True),
    (waste_mask.selfMask(),      {"palette": "#800080"}, "🟣 Waste / Plastic Anomaly",True),
]

for img, vis, name, show in layers:
    folium.TileLayer(
        tiles=_tile(img, vis), attr="GEE",
        name=name, overlay=True, control=True, show=show,
    ).add_to(Map)

folium.LayerControl(collapsed=False).add_to(Map)

print("Map ready. Call Map to display in a Jupyter notebook, or Map.save('map.html').")
Map.save("outputs/change_detection_map.html")
print("Saved → outputs/change_detection_map.html")
