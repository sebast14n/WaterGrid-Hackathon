"""
WaterGrid - Monitoring Natura 2000 sites with Copernicus & Galileo
"""
from __future__ import annotations
import os
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

API_URL = os.getenv("WATERGRID_API_URL", "http://api:8000/api")
KMZ_ID = 1

st.set_page_config(
    page_title="WaterGrid - Natura 2000 Monitoring",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.main-header {
    background: linear-gradient(135deg, #003399 0%, #1e3a8a 100%);
    padding: 1.5rem;
    border-radius: 8px;
    color: white;
    margin-bottom: 1rem;
}
.badge {
    display: inline-block;
    padding: 0.3rem 0.8rem;
    background: #FFCC00;
    color: #003399;
    border-radius: 4px;
    font-weight: bold;
    font-size: 0.85rem;
    margin-right: 0.5rem;
}
.alert-high { background: #fee2e2; border-left: 4px solid #dc2626; padding: 0.8rem; border-radius: 6px; margin: 0.3rem 0; }
.alert-medium { background: #fef3c7; border-left: 4px solid #d97706; padding: 0.8rem; border-radius: 6px; margin: 0.3rem 0; }
.alert-low { background: #dbeafe; border-left: 4px solid #2563eb; padding: 0.8rem; border-radius: 6px; margin: 0.3rem 0; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <span class="badge">🇪🇺 COPERNICUS</span>
    <span class="badge">📡 GALILEO</span>
    <span class="badge">🛰️ EGNOS</span>
    <h1 style="margin: 0.5rem 0 0 0;">WaterGrid</h1>
    <p style="margin: 0; opacity: 0.9;">Automated detection of anthropic disturbances in Natura 2000 protected sites</p>
</div>
""", unsafe_allow_html=True)


@st.cache_data(ttl=60)
def fetch_api(endpoint):
    try:
        r = requests.get(API_URL + endpoint, timeout=30)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        st.error("API error: " + str(e))
        return None


def fetch_post(endpoint):
    try:
        r = requests.get(API_URL + endpoint, timeout=120)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        st.error("API error: " + str(e))
        return None


st.sidebar.title("Controls")
st.sidebar.markdown("---")

aoi_info = fetch_api("/aoi/" + str(KMZ_ID))

if aoi_info:
    st.sidebar.markdown("**Site:** " + aoi_info['name'])
    st.sidebar.markdown("**Area:** " + str(round(aoi_info['area_ha'], 1)) + " ha")
    lat = aoi_info['centroid']['lat']
    lon = aoi_info['centroid']['lon']
    st.sidebar.markdown("**Center:** " + str(round(lat, 4)) + "°N, " + str(round(lon, 4)) + "°E")

view_mode = st.sidebar.radio(
    "View",
    ["📊 Dashboard", "🗺️ Map & Imagery", "🚨 Alerts & Reports", "📹 Drone Evidence", "ℹ️ About"]
)

st.sidebar.markdown("---")
st.sidebar.markdown("**Powered by:**")
st.sidebar.markdown("- ESA Copernicus Sentinel-2")
st.sidebar.markdown("- Galileo GNSS (drone georef)")
st.sidebar.markdown("- Anthropic Claude (LLM reports)")


if view_mode == "📊 Dashboard":
    monthly = fetch_api("/timeseries/" + str(KMZ_ID) + "/monthly")
    alerts_data = fetch_api("/alerts/" + str(KMZ_ID))
    timeseries_data = fetch_api("/timeseries/" + str(KMZ_ID))
    
    col1, col2, col3, col4 = st.columns(4)
    
    if aoi_info:
        with col1:
            st.metric("Monitored area", str(round(aoi_info['area_ha'])) + " ha")
    
    if timeseries_data:
        with col2:
            st.metric("Sentinel-2 scenes", timeseries_data['count'])
    
    if monthly:
        n_months = len(monthly['monthly'])
        with col3:
            st.metric("Months analyzed", n_months)
    
    if alerts_data:
        with col4:
            n_alerts = alerts_data['total']
            st.metric("Anomalies detected", n_alerts)
    
    st.markdown("---")
    st.subheader("📈 Time series — 2 years of Sentinel-2 monitoring")
    
    if monthly and monthly['monthly']:
        df = pd.DataFrame(monthly['monthly'])
        
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=("Vegetation & Soil indicators (NDVI / BSI)", "Water surface (NDWI / Water %)"),
            vertical_spacing=0.15,
            specs=[[{"secondary_y": False}], [{"secondary_y": True}]]
        )
        
        fig.add_trace(
            go.Scatter(x=df['month'], y=df['ndvi'], name='NDVI (vegetation)',
                      line=dict(color='#22c55e', width=2.5), mode='lines+markers'),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(x=df['month'], y=df['bsi'], name='BSI (bare soil)',
                      line=dict(color='#dc2626', width=2.5), mode='lines+markers'),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(x=df['month'], y=df['ndwi'], name='NDWI (water signal)',
                      line=dict(color='#0ea5e9', width=2.5), mode='lines+markers'),
            row=2, col=1
        )
        fig.add_trace(
            go.Bar(x=df['month'], y=df['water_pct'], name='Water surface %',
                   marker_color='rgba(14, 165, 233, 0.3)'),
            row=2, col=1, secondary_y=True
        )
        
        fig.update_xaxes(tickangle=-45)
        fig.update_layout(height=600, hovermode='x unified', showlegend=True,
                         legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        
        st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("---")
    st.subheader("🎬 Visual evolution — monthly composite animation")
    
    anim = fetch_api("/animation/" + str(KMZ_ID))
    if anim and anim.get('exists'):
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image(anim['url'], use_container_width=True)
            if aoi_info:
                st.caption("24 months of Sentinel-2 imagery — " + aoi_info['name'])
    else:
        st.info("Animation not yet generated.")


elif view_mode == "🗺️ Map & Imagery":
    if aoi_info:
        st.subheader("🗺️ Map — " + aoi_info['name'])
        
        m = folium.Map(
            location=[aoi_info['centroid']['lat'], aoi_info['centroid']['lon']],
            zoom_start=15,
            tiles='https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2021_3857/default/g/{z}/{y}/{x}.jpg',
            attr='Sentinel-2 cloudless – s2maps.eu by EOX'
        )
        
        folium.GeoJson(
            aoi_info['geojson'],
            name="Monitored zone",
            style_function=lambda _: {"color": "#dc2626", "weight": 3, "fillColor": "#dc2626", "fillOpacity": 0.2}
        ).add_to(m)
        
        folium.LayerControl().add_to(m)
        st_folium(m, height=500, use_container_width=True)
    
    st.markdown("---")
    st.subheader("🖼️ Sentinel-2 imagery — by month")
    
    images = fetch_api("/scene-images/" + str(KMZ_ID))
    if images:
        df_img = pd.DataFrame(images)
        df_img['month'] = df_img['date'].str[:7]
        best_per_month = df_img.sort_values('cloud_cover').groupby('month').first().reset_index()
        best_per_month = best_per_month.sort_values('month')
        
        st.markdown("**" + str(len(best_per_month)) + " monthly composites** — best cloud-free scene per month")
        
        view_type = st.selectbox("Imagery type", ["RGB true color", "NDVI (vegetation)", "NDWI (water)"])
        url_field = {"RGB true color": "rgb_url", "NDVI (vegetation)": "ndvi_url", "NDWI (water)": "ndwi_url"}[view_type]
        
        n_cols = 4
        for i in range(0, len(best_per_month), n_cols):
            cols = st.columns(n_cols)
            for j, col in enumerate(cols):
                if i + j < len(best_per_month):
                    row = best_per_month.iloc[i + j]
                    with col:
                        if row[url_field]:
                            st.image(row[url_field], caption=row['month'], use_container_width=True)
                            caption = "NDVI: " + str(row['ndvi']) + " | BSI: " + str(row['bsi']) + " | Water: " + str(row['water_pct']) + "%"
                            st.caption(caption)


elif view_mode == "🚨 Alerts & Reports":
    st.subheader("🚨 Detected anomalies")
    st.markdown("Anomalies flagged when monthly indices deviate (z-score > 1.0) from 12-month baseline.")
    
    alerts_data = fetch_api("/alerts/" + str(KMZ_ID))
    
    if alerts_data and alerts_data['alerts']:
        for alert in alerts_data['alerts']:
            sev = alert['severity']
            css_class = "alert-" + sev
            
            html = '<div class="' + css_class + '"><strong>' + alert['month'] + '</strong> — Severity: ' + sev.upper()
            html += ' (max z-score: ' + str(alert['max_z']) + ')<br>'
            html += 'NDVI: ' + str(alert['ndvi']) + ' (z=' + str(alert['ndvi_z']) + ') | '
            html += 'BSI: ' + str(alert['bsi']) + ' (z=' + str(alert['bsi_z']) + ') | '
            html += 'NDWI: ' + str(alert['ndwi']) + ' (z=' + str(alert['ndwi_z']) + ')</div>'
            st.markdown(html, unsafe_allow_html=True)
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("📄 Generate report", key="report_" + alert['month']):
                    with st.spinner("Generating official report via Claude..."):
                        report = fetch_post("/alerts/" + str(KMZ_ID) + "/" + alert['month'] + "/report")
                        if report:
                            st.session_state["report_" + alert['month']] = report
            
            session_key = "report_" + alert['month']
            if session_key in st.session_state:
                rep = st.session_state[session_key]
                with st.expander("📄 Official report — " + alert['month'], expanded=True):
                    st.markdown(rep['report'])
                    st.markdown("---")
                    st.caption("Generated by Claude (Anthropic) — Data: " + rep['metadata']['data_source'])
                    st.download_button(
                        "Download as text",
                        rep['report'],
                        file_name="watergrid_report_" + alert['month'] + ".txt"
                    )
            
            st.markdown("---")
    else:
        st.info("No anomalies detected with current thresholds.")


elif view_mode == "📹 Drone Evidence":
    st.subheader("📹 Ground truth — Drone footage")
    st.markdown("""
Drone footage validates satellite-detected anomalies on the ground.
Drone position is georeferenced using **Galileo GNSS + EGNOS** for sub-metric accuracy.
""")
    
    drone_dir = "/data/drone"
    if os.path.exists(drone_dir):
        videos = [f for f in os.listdir(drone_dir) if f.endswith('.mp4')]
        if videos:
            for video in sorted(videos):
                title = video.replace('_', ' ').replace('.mp4', '').title()
                st.markdown("**" + title + "**")
                st.video("/drone/" + video)
                st.markdown("---")
        else:
            st.warning("No drone videos yet. Add files to /data/drone/")
    else:
        st.warning("Drone directory not found.")


elif view_mode == "ℹ️ About":
    st.subheader("ℹ️ About WaterGrid")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("""
### The problem

Romania has 600+ Natura 2000 sites covering ~23% of the country.
These protected ecosystems are increasingly under pressure from illegal activities:

- 🏗️ Unauthorized aggregate extraction (balastiere)
- 🗑️ Construction debris dumping
- 💧 River bed alteration

Authorities cannot physically monitor every site.
Field inspections happen rarely.

### Our solution

WaterGrid uses **only public European space infrastructure** to provide
continuous, automated monitoring of protected sites.
""")
    
    with col2:
        st.markdown("""
### European data sources

🛰️ **Copernicus Sentinel-2**
Optical imagery, 10m resolution, 5-day revisit.

📡 **Galileo GNSS**
Drone georeferencing for ground-truth validation.

🛰️ **EGNOS**
Augmentation system for accuracy.

🤖 **Anthropic Claude**
LLM-generated official reports.

### Pilot site

ROSCI0434 - Siretul Mijlociu
~30 km² of riverbank ecosystem
33 ha around documented illegal balastiera
""")
