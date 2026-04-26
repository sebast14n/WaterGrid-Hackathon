"""
Streamlit app for land change detection using geoai-py.
- Sidebar: select year pair and trigger downloads / change detection
- Map: interactive Leafmap/folium view of AOI + change overlay
- Gallery: before / after / change images side by side
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
import folium
from streamlit_folium import st_folium

import calendar

from config import AOI_BBOX, YEARS, TIFF_DIR, CHANGE_DIR, get_aoi_geojson
from download_images import download_year, download_month, download_all_months, MONTH_NAMES
from land_change import run_change_detection
from visualize import (
    tif_to_rgb_png,
    tif_to_ndvi_png,
    make_timelapse_gif,
    ndvi_timeseries_png,
    diff_map_png,
)

st.set_page_config(page_title="Land Change Detection", layout="wide")
st.title("Land Change Detection — geoai + Planetary Computer")

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.header("Settings")

year_options = YEARS
year_before = st.sidebar.selectbox("Year (before)", year_options[:-1], index=0)
year_after  = st.sidebar.selectbox("Year (after)",  year_options[1:],  index=0)

overwrite = st.sidebar.checkbox("Re-download / re-run", value=False)

if st.sidebar.button("1. Download imagery"):
    with st.spinner(f"Downloading {year_before} ..."):
        download_year(year_before, overwrite=overwrite)
    with st.spinner(f"Downloading {year_after} ..."):
        download_year(year_after, overwrite=overwrite)
    st.sidebar.success("Download complete.")

if st.sidebar.button("2. Run change detection"):
    with st.spinner("Running ChangeStarDetection ..."):
        run_change_detection(year_before, year_after, overwrite=overwrite)
    st.sidebar.success("Change detection complete.")

# ── Monthly download section ──────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("Monthly Download")

all_years = list(range(2017, 2026))   # Sentinel-2 L2A starts ~2017
monthly_year = st.sidebar.selectbox("Year", all_years, index=all_years.index(2020))

month_labels = [f"{i:02d} – {MONTH_NAMES[i-1]}" for i in range(1, 13)]
selected_months_labels = st.sidebar.multiselect(
    "Months (leave empty = all 12)",
    month_labels,
    default=[],
)
# Parse selected month numbers; default to all 12
selected_months = (
    [int(m.split(" – ")[0]) for m in selected_months_labels]
    if selected_months_labels
    else list(range(1, 13))
)

monthly_overwrite = st.sidebar.checkbox("Re-download monthly", value=False)

if st.sidebar.button("Download monthly imagery"):
    progress = st.sidebar.progress(0, text="Starting...")
    results = []
    for idx, month in enumerate(selected_months):
        mm = calendar.month_abbr[month]
        progress.progress((idx) / len(selected_months), text=f"Downloading {monthly_year}-{mm} ...")
        p = download_month(monthly_year, month, overwrite=monthly_overwrite)
        results.append((month, p))
    progress.progress(1.0, text="Done!")
    ok  = sum(1 for _, p in results if p)
    fail = sum(1 for _, p in results if not p)
    st.sidebar.success(f"Downloaded {ok}/{len(selected_months)} months.")
    if fail:
        st.sidebar.warning(f"{fail} month(s) had no scenes available.")

# ── Map ───────────────────────────────────────────────────────────────────────
st.subheader("AOI Map")

center_lat = (AOI_BBOX[1] + AOI_BBOX[3]) / 2
center_lon = (AOI_BBOX[0] + AOI_BBOX[2]) / 2

# Basemap selector
basemap_choice = st.sidebar.selectbox(
    "Basemap",
    ["Sentinel-2 Cloudless (Copernicus/EOX)", "OpenStreetMap", "Google Satellite"],
    index=0,
)

BASEMAPS = {
    "Sentinel-2 Cloudless (Copernicus/EOX)": {
        "tiles": "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2021_3857/default/g/{z}/{y}/{x}.jpg",
        "attr": (
            '&copy; <a href="https://s2maps.eu">Sentinel-2 cloudless – s2maps.eu</a> '
            'by <a href="https://eox.at">EOX IT Services GmbH</a> '
            '(Contains modified Copernicus Sentinel data 2021)'
        ),
    },
    "OpenStreetMap": {
        "tiles": "OpenStreetMap",
        "attr": "&copy; OpenStreetMap contributors",
    },
    "Google Satellite": {
        "tiles": "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        "attr": "Google Satellite",
    },
}

bm = BASEMAPS[basemap_choice]
m = folium.Map(
    location=[center_lat, center_lon],
    zoom_start=14,
    tiles=bm["tiles"],
    attr=bm["attr"],
)

# Draw AOI polygon
aoi_geojson = get_aoi_geojson()
folium.GeoJson(
    aoi_geojson,
    name="AOI",
    style_function=lambda _: {"color": "cyan", "weight": 2, "fillOpacity": 0},
).add_to(m)

folium.LayerControl().add_to(m)
st_folium(m, height=420, width="stretch")

# ══ Tabs ══════════════════════════════════════════════════════════════════════
tab_download, tab_visual, tab_gee, tab_files = st.tabs(
    ["📥 Download status", "🎞️ Visualize", "🛰️ GEE Analysis", "📂 Files"]
)

# ── Tab 1: download status ─────────────────────────────────────────────────
with tab_download:
    st.subheader("Yearly imagery")
    before_tif  = TIFF_DIR  / f"sentinel2_{year_before}.tif"
    after_tif   = TIFF_DIR  / f"sentinel2_{year_after}.tif"
    change_tif  = CHANGE_DIR / f"change_{year_before}_{year_after}.tif"
    change_gpkg = CHANGE_DIR / f"change_{year_before}_{year_after}.gpkg"

    cols = st.columns(3)
    with cols[0]:
        st.markdown(f"**Before ({year_before})**")
        if before_tif.exists():
            st.success(f"✓ `{before_tif.name}`")
        else:
            st.warning("Not downloaded yet.")
    with cols[1]:
        st.markdown(f"**After ({year_after})**")
        if after_tif.exists():
            st.success(f"✓ `{after_tif.name}`")
        else:
            st.warning("Not downloaded yet.")
    with cols[2]:
        st.markdown("**Change mask**")
        if change_tif.exists():
            st.success(f"✓ `{change_tif.name}`")
            if change_gpkg.exists():
                st.info(f"Vector: `{change_gpkg.name}`")
        else:
            st.warning("Not computed yet.")

    st.markdown("---")
    st.subheader(f"Monthly imagery — {monthly_year}")
    monthly_tifs = sorted(TIFF_DIR.glob(f"sentinel2_{monthly_year}_??.tif"))
    if not monthly_tifs:
        st.info("No monthly GeoTIFFs yet — use 'Download monthly imagery' in the sidebar.")
    else:
        cols = st.columns(4)
        for i, tif in enumerate(monthly_tifs):
            month_num = int(tif.stem.split("_")[-1])
            label = f"{calendar.month_name[month_num]} {monthly_year}"
            size_mb = tif.stat().st_size / 1e6
            with cols[i % 4]:
                st.success(f"**{label}**")
                st.caption(f"{size_mb:.0f} MB")

# ── Tab 2: Visualize ───────────────────────────────────────────────────────
with tab_visual:
    # ── Collect ALL available GeoTIFFs (yearly + monthly) ─────────────────
    def _tif_label(p: Path) -> str:
        """Human-readable label for any sentinel2_*.tif filename."""
        parts = p.stem.split("_")   # ["sentinel2", YYYY] or ["sentinel2", YYYY, MM]
        if len(parts) == 3:         # monthly
            yr, mm = int(parts[1]), int(parts[2])
            return f"{calendar.month_abbr[mm]} {yr}"
        elif len(parts) == 2:       # yearly
            return f"Year {parts[1]}"
        return p.stem

    all_tifs_raw = sorted(TIFF_DIR.glob("sentinel2_*.tif"))
    # Build label → path mapping (deduplicated)
    all_tifs_map: dict[str, Path] = {_tif_label(p): p for p in all_tifs_raw}
    all_labels = list(all_tifs_map.keys())

    # Let the user pick which images to include in visualizations
    if all_tifs_raw:
        selected_labels = st.multiselect(
            "🗂️ Select images to visualize (default = all)",
            all_labels,
            default=all_labels,
            key="viz_selector",
        )
    else:
        selected_labels = []

    all_monthly = [all_tifs_map[l] for l in selected_labels if l in all_tifs_map]

    if not all_monthly:
        st.info("No GeoTIFFs selected. Download some first or adjust the selector above.")
    else:
        viz_mode = st.radio(
            "Visualization mode",
            ["🖼️ Monthly RGB gallery",
             "🌿 Monthly NDVI gallery",
             "📈 NDVI time series",
             "🎞️ Animated timelapse GIF",
             "🔀 Before / After diff map"],
            horizontal=False,
        )

        # ── RGB gallery ───────────────────────────────────────────────────
        if viz_mode.startswith("🖼️"):
            st.caption("Each image is cropped to the AOI bounding box.")
            cols = st.columns(4)
            for i, tif in enumerate(all_monthly):
                label = _tif_label(tif)
                with cols[i % 4]:
                    with st.spinner(f"{label} ..."):
                        try:
                            png = tif_to_rgb_png(tif)
                            st.image(png, caption=label, width="stretch")
                        except Exception as e:
                            st.error(f"{label}: {e}")

        # ── NDVI gallery ──────────────────────────────────────────────────
        elif viz_mode.startswith("🌿"):
            st.caption("NDVI colourmap: green = healthy vegetation, red = bare/stressed.")
            cols = st.columns(4)
            for i, tif in enumerate(all_monthly):
                label = _tif_label(tif)
                with cols[i % 4]:
                    with st.spinner(f"{label} ..."):
                        try:
                            png = tif_to_ndvi_png(tif)
                            st.image(png, caption=label, width="stretch")
                        except Exception as e:
                            st.error(f"{label}: {e}")

        # ── NDVI time series ──────────────────────────────────────────────
        elif viz_mode.startswith("📈"):
            st.caption("Mean NDVI across pixels in the AOI, plotted over time.")
            with st.spinner("Computing NDVI for all months ..."):
                labels = [_tif_label(p) for p in all_monthly]
                try:
                    chart_png = ndvi_timeseries_png(all_monthly, labels)
                    st.image(chart_png, width="stretch")
                except Exception as e:
                    st.error(str(e))

        # ── Timelapse GIF ─────────────────────────────────────────────────
        elif viz_mode.startswith("🎞️"):
            fps = st.slider("Frame speed (fps)", 0.5, 4.0, 1.5, 0.5)
            if st.button("Generate timelapse GIF"):
                with st.spinner("Rendering frames ..."):
                    labels = [_tif_label(p) for p in all_monthly]
                    try:
                        gif = make_timelapse_gif(all_monthly, fps=fps, labels=labels)
                        st.image(gif, caption="Sentinel-2 timelapse", width="stretch")
                        st.download_button("⬇️ Download GIF", gif, "timelapse.gif", "image/gif")
                    except Exception as e:
                        st.error(str(e))
            else:
                st.info("Press 'Generate timelapse GIF' to build the animation.")

        # ── Before / After diff map ───────────────────────────────────────
        elif viz_mode.startswith("🔀"):
            labels_map = {_tif_label(p): p for p in all_monthly}
            label_list = list(labels_map.keys())
            if len(label_list) < 2:
                st.warning("Need at least 2 monthly GeoTIFFs to compare.")
            else:
                col_a, col_b = st.columns(2)
                with col_a:
                    label_before = st.selectbox("Before", label_list, index=0)
                with col_b:
                    label_after  = st.selectbox("After",  label_list, index=min(1, len(label_list)-1))

                if st.button("Compute difference"):
                    if label_before == label_after:
                        st.warning("Please select two different months.")
                    else:
                        with st.spinner("Computing NDVI difference ..."):
                            try:
                                png = diff_map_png(
                                    labels_map[label_before],
                                    labels_map[label_after],
                                    label_before,
                                    label_after,
                                )
                                st.image(png, width="stretch")
                                st.caption(
                                    "Left: RGB before | Middle: RGB after | "
                                    "Right: NDVI change (green=vegetation gain, red=loss)"
                                )
                                st.download_button(
                                    "⬇️ Download diff map",
                                    png,
                                    f"diff_{label_before}_{label_after}.png".replace(" ", "_"),
                                    "image/png",
                                )
                            except Exception as e:
                                st.error(str(e))

# ── Tab 3: GEE Analysis ───────────────────────────────────────────────────
with tab_gee:
    st.subheader("🛰️ Google Earth Engine — Illegal Land Change Detection")
    st.markdown("""
    Uses **Sentinel-2 L2A** via Google Earth Engine with dual cloud masking
    (SCL band + s2cloudless probability) to detect:

    | Anomaly | Index | Threshold |
    |---|---|---|
    | 🔴 Mine / excavation | ΔBSI > +0.05 | New bare soil |
    | 🟠 Vegetation loss | ΔNDVI < −0.10 | Forest clearing |
    | 🟣 Waste / plastic | ΔPI > +0.15 | Spectral anomaly |
    """)

    gee_col1, gee_col2 = st.columns(2)
    with gee_col1:
        gee_year_before = st.selectbox("Before year", list(range(2019, 2026)), index=4, key="gee_before")
    with gee_col2:
        gee_year_after  = st.selectbox("After year",  list(range(2019, 2026)), index=5, key="gee_after")

    gee_season_start = st.select_slider(
        "Season start (MM-DD)", options=["03-01","04-01","05-01","06-01"], value="05-01"
    )
    gee_season_end = st.select_slider(
        "Season end (MM-DD)", options=["08-31","09-30","10-31","11-30"], value="09-30"
    )

    gee_cloud_max      = st.slider("Max scene cloud cover (%)", 1, 30, 10)
    gee_cloud_prob_thr = st.slider("s2cloudless pixel threshold", 10, 80, 40)
    bsi_thr  = st.slider("BSI change threshold",   0.01, 0.20, 0.05, 0.01)
    ndvi_thr = st.slider("NDVI loss threshold",   -0.30, -0.05, -0.10, 0.01)
    pi_thr   = st.slider("Plastic index threshold", 0.05, 0.40, 0.15, 0.01)

    if st.button("▶ Run GEE Analysis"):
        try:
            import ee, geemap

            with st.spinner("Initialising Google Earth Engine ..."):
                try:
                    ee.Initialize(project="project-ult-60cf8")
                except Exception:
                    ee.Authenticate()
                    ee.Initialize(project="project-ult-60cf8")

            roi = ee.Geometry.BBox(
                west=26.94418657349121, south=46.45436033015628,
                east=27.00760413540865, north=46.65837577224148,
            )

            def _scl_mask(img):
                scl = img.select("SCL")
                bad = (scl.eq(0).Or(scl.eq(1)).Or(scl.eq(3))
                          .Or(scl.eq(8)).Or(scl.eq(9))
                          .Or(scl.eq(10)).Or(scl.eq(11)))
                return img.updateMask(bad.Not())

            def _build(year):
                s2 = (
                    ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                    .filterBounds(roi)
                    .filterDate(f"{year}-{gee_season_start}", f"{year}-{gee_season_end}")
                    .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", gee_cloud_max))
                )
                cp = (
                    ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY")
                    .filterBounds(roi)
                    .filterDate(f"{year}-{gee_season_start}", f"{year}-{gee_season_end}")
                )
                joined = ee.ImageCollection(
                    ee.Join.saveFirst("cp").apply(
                        s2, cp,
                        ee.Filter.equals(leftField="system:index", rightField="system:index"),
                    )
                )
                def mask(img):
                    img = _scl_mask(img)
                    prob = ee.Image(img.get("cp")).select("probability")
                    return img.updateMask(prob.lt(gee_cloud_prob_thr))
                return joined.map(mask).median().clip(roi)

            with st.spinner(f"Building {gee_year_before} composite ..."):
                before = _build(gee_year_before)
            with st.spinner(f"Building {gee_year_after} composite ..."):
                after  = _build(gee_year_after)

            # Indices
            def bsi(img):
                return (img.select("B11").add(img.select("B4"))
                           .subtract(img.select("B8").add(img.select("B2")))
                           .divide(img.select("B11").add(img.select("B4"))
                                      .add(img.select("B8")).add(img.select("B2")))
                           .rename("BSI"))

            def ndvi(img):
                return img.normalizedDifference(["B8", "B4"]).rename("NDVI")

            def pi(img):
                nir = img.select("B8"); swir2 = img.select("B12")
                return nir.divide(nir.add(swir2)).rename("PI")

            d_bsi  = bsi(after).subtract(bsi(before)).rename("dBSI")
            d_ndvi = ndvi(after).subtract(ndvi(before)).rename("dNDVI")
            d_pi   = pi(after).subtract(pi(before)).rename("dPI")

            excav = d_bsi.gt(bsi_thr).selfMask()
            vloss = d_ndvi.lt(ndvi_thr).And(d_bsi.gt(0)).selfMask()
            water = after.normalizedDifference(["B3", "B8"]).gt(0.1)
            waste = d_pi.gt(pi_thr).And(water.Not()).selfMask()

            with st.spinner("Building interactive map ..."):
                # Use plain folium with GEE tile URLs — no Jupyter kernel needed
                def _tile(img, vis):
                    vis_str = {k: ",".join(v) if isinstance(v, list) else str(v)
                               for k, v in vis.items()}
                    map_id = ee.data.getMapId({**vis_str, "image": img})
                    return map_id["tile_fetcher"].url_format

                fmap = folium.Map(
                    location=[46.556, 26.976], zoom_start=11,
                    tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
                    attr="Google Satellite",
                )
                layers = [
                    (after,  {"bands":"B4,B3,B2","min":"0","max":"3000","gamma":"1.4"}, f"RGB {gee_year_after}", True),
                    (before, {"bands":"B4,B3,B2","min":"0","max":"3000","gamma":"1.4"}, f"RGB {gee_year_before}", False),
                    (d_bsi,  {"min":"-0.2","max":"0.2","palette":"#1a9641,#ffffbf,#d7191c"}, "ΔBSI", True),
                    (d_ndvi, {"min":"-0.4","max":"0.4","palette":"#d7191c,#ffffbf,#1a9641"}, "ΔNDVI", False),
                    (excav,  {"palette":"#FF0000"}, "🔴 Excavation / Mine", True),
                    (vloss,  {"palette":"#FF8C00"}, "🟠 Vegetation Loss", True),
                    (waste,  {"palette":"#800080"}, "🟣 Waste Anomaly", True),
                ]
                for img, vis, name, show in layers:
                    folium.TileLayer(
                        tiles=_tile(img, vis), attr="GEE",
                        name=name, overlay=True, control=True, show=show,
                    ).add_to(fmap)
                folium.LayerControl(collapsed=False).add_to(fmap)
                out_html = Path("outputs/change") / f"gee_{gee_year_before}_{gee_year_after}.html"
                fmap.save(str(out_html))

            st.success(f"Analysis complete — {gee_year_before} → {gee_year_after}")
            with open(out_html) as f:
                st.components.v1.html(f.read(), height=600, scrolling=True)

            st.download_button("⬇️ Download interactive map", open(out_html).read(),
                               out_html.name, "text/html")

        except ImportError:
            st.error("Install dependencies: `pip install earthengine-api geemap`")
        except Exception as exc:
            st.error(f"GEE error: {exc}")
            st.info("Make sure you have run `earthengine authenticate` and have a valid GEE project.")

# ── Tab 4: Files ───────────────────────────────────────────────────────────
with tab_files:
    tiffs   = sorted(TIFF_DIR.glob("sentinel2_*.tif"))
    changes = sorted(CHANGE_DIR.glob("change_*.tif"))
    for f in tiffs + changes:
        size_mb = f.stat().st_size / 1e6
        st.text(f"{f.name}  ({size_mb:.1f} MB)")


# Streamlit execution protection
if __name__ == '__main__':
    pass  # Streamlit handles execution

