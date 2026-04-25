import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go

st.title("🛰️ WaterGrid - Natura 2000 Monitoring")
st.markdown("**Automated satellite monitoring for protected natural sites**")

API_URL = "http://api:8000/api"

@st.cache_data(ttl=60)
def get_data(endpoint):
    try:
        r = requests.get(API_URL + endpoint, timeout=20)
        return r.json() if r.status_code == 200 else None
    except:
        return None

# Sidebar
mode = st.sidebar.selectbox("View", ["Dashboard", "Gallery", "Reports"])

if mode == "Dashboard":
    st.subheader("📊 Dashboard")
    
    data = get_data("/timeseries/1/monthly")
    if data and data['monthly']:
        df = pd.DataFrame(data['monthly'])
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df['month'], y=df['ndvi'], name='NDVI', line=dict(color='green')))
        fig.add_trace(go.Scatter(x=df['month'], y=df['bsi'], name='BSI', line=dict(color='red')))
        fig.update_layout(title="Spectral Indices - 24 Months", height=400)
        
        st.plotly_chart(fig, use_container_width=True)
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Scenes Processed", len(df))
        with col2:
            st.metric("Latest NDVI", f"{df.iloc[-1]['ndvi']:.3f}")
        with col3:
            st.metric("Latest BSI", f"{df.iloc[-1]['bsi']:.3f}")
    else:
        st.error("No data available")

elif mode == "Gallery":
    st.subheader("🖼️ Satellite Gallery")
    st.info("24-month evolution animation available")
    
    # Show some sample images if available
    images = get_data("/scene-images/1")
    if images:
        st.write(f"**{len(images)} satellite scenes available**")
        for i, img in enumerate(images[:3]):
            st.write(f"📅 {img.get('date', 'Unknown')}: {img.get('scene_id', 'Unknown')}")

elif mode == "Reports":
    st.subheader("🚨 Anomaly Reports")
    
    alerts = get_data("/alerts/1")
    if alerts and alerts.get('alerts'):
        st.write(f"**{len(alerts['alerts'])} anomalies detected**")
        
        for alert in alerts['alerts']:
            st.warning(f"**{alert['month']}**: NDVI={alert['ndvi']:.3f}, BSI={alert['bsi']:.3f}")
            
            if st.button(f"Generate Report for {alert['month']}", key=alert['month']):
                st.info("Report generation takes ~30 seconds...")
                try:
                    resp = requests.get(f"{API_URL}/alerts/1/{alert['month']}/report", timeout=60)
                    if resp.status_code == 200:
                        report = resp.json()
                        if 'report' in report:
                            st.success("✅ Report generated!")
                            st.text_area("Official Report", report['report'], height=300)
                        else:
                            st.error("Invalid report format")
                    else:
                        st.error(f"HTTP {resp.status_code}")
                except Exception as e:
                    st.error(f"Error: {e}")
    else:
        st.success("✅ No anomalies detected")

st.sidebar.markdown("---")
st.sidebar.info("🇪🇺 Copernicus Sentinel-2 Data\n📡 Galileo GNSS\n🤖 Claude AI Reports")
