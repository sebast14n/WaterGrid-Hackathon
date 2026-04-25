"""
WaterGrid API - serves data from PostGIS to Streamlit UI.
"""
from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS
import psycopg
import os
import json
from datetime import datetime
from anthropic import Anthropic

app = Flask(__name__)
CORS(app)  # permite Streamlit să facă fetch

DB_URL = os.getenv("DATABASE_URL")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
OUTPUT_DIR = "/data/kmz_outputs"


@app.route('/api/health')
def health():
    return jsonify({"status": "ok", "service": "watergrid-api"})


@app.route('/api/aoi/<int:kmz_id>')
def aoi_info(kmz_id):
    """Info despre zona KMZ."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT name, description,
                       ST_Area(geom::geography) / 10000 AS area_ha,
                       ST_AsGeoJSON(geom) AS geojson,
                       ST_Y(ST_Centroid(geom)) AS lat,
                       ST_X(ST_Centroid(geom)) AS lon
                FROM kmz_aois WHERE id = %s
            """, (kmz_id,))
            row = cur.fetchone()
    
    if not row:
        return jsonify({"error": "Not found"}), 404
    
    return jsonify({
        "id": kmz_id,
        "name": row[0],
        "description": row[1],
        "area_ha": float(row[2]),
        "geojson": json.loads(row[3]),
        "centroid": {"lat": float(row[4]), "lon": float(row[5])}
    })


@app.route('/api/timeseries/<int:kmz_id>')
def timeseries(kmz_id):
    """Serie temporală - toate observațiile."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    observed_at,
                    scene_id,
                    cloud_cover,
                    ndvi_mean,
                    bsi_mean,
                    ndwi_mean,
                    water_pixel_pct,
                    pixel_count,
                    rgb_path,
                    ndvi_path,
                    ndwi_path
                FROM kmz_observations
                WHERE kmz_id = %s
                ORDER BY observed_at ASC
            """, (kmz_id,))
            rows = cur.fetchall()
    
    return jsonify({
        "kmz_id": kmz_id,
        "count": len(rows),
        "observations": [
            {
                "observed_at": row[0].isoformat() if row[0] else None,
                "date": row[0].strftime("%Y-%m-%d") if row[0] else None,
                "scene_id": row[1],
                "cloud_cover": float(row[2]) if row[2] is not None else None,
                "ndvi": float(row[3]) if row[3] is not None else None,
                "bsi": float(row[4]) if row[4] is not None else None,
                "ndwi": float(row[5]) if row[5] is not None else None,
                "water_pct": float(row[6]) if row[6] is not None else None,
                "pixel_count": row[7],
                "rgb_filename": os.path.basename(row[8]) if row[8] else None,
                "ndvi_filename": os.path.basename(row[9]) if row[9] else None,
                "ndwi_filename": os.path.basename(row[10]) if row[10] else None,
            }
            for row in rows
        ]
    })


@app.route('/api/timeseries/<int:kmz_id>/monthly')
def timeseries_monthly(kmz_id):
    """Aggregare lunară pentru charts."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    TO_CHAR(date_trunc('month', observed_at), 'YYYY-MM') AS month,
                    COUNT(*) AS n_scenes,
                    AVG(ndvi_mean) AS ndvi,
                    AVG(bsi_mean) AS bsi,
                    AVG(ndwi_mean) AS ndwi,
                    AVG(water_pixel_pct) AS water_pct,
                    MAX(water_pixel_pct) AS max_water,
                    MIN(ndvi_mean) AS min_ndvi,
                    MAX(bsi_mean) AS max_bsi
                FROM kmz_observations
                WHERE kmz_id = %s
                GROUP BY date_trunc('month', observed_at)
                ORDER BY month
            """, (kmz_id,))
            rows = cur.fetchall()
    
    return jsonify({
        "kmz_id": kmz_id,
        "monthly": [
            {
                "month": row[0],
                "n_scenes": row[1],
                "ndvi": round(float(row[2]), 3) if row[2] else None,
                "bsi": round(float(row[3]), 3) if row[3] else None,
                "ndwi": round(float(row[4]), 3) if row[4] else None,
                "water_pct": round(float(row[5]), 1) if row[5] else None,
                "max_water": round(float(row[6]), 1) if row[6] else None,
                "min_ndvi": round(float(row[7]), 3) if row[7] else None,
                "max_bsi": round(float(row[8]), 3) if row[8] else None,
            }
            for row in rows
        ]
    })


@app.route('/api/alerts/<int:kmz_id>')
def alerts(kmz_id):
    """Alerte detectate prin z-score față de baseline 12 luni."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH monthly AS (
                    SELECT 
                        date_trunc('month', observed_at) AS month_dt,
                        AVG(ndvi_mean) AS ndvi,
                        AVG(bsi_mean) AS bsi,
                        AVG(ndwi_mean) AS ndwi,
                        AVG(water_pixel_pct) AS water_pct
                    FROM kmz_observations
                    WHERE kmz_id = %s
                    GROUP BY date_trunc('month', observed_at)
                ),
                baseline AS (
                    SELECT 
                        AVG(ndvi) AS ndvi_mean, STDDEV(ndvi) AS ndvi_std,
                        AVG(bsi) AS bsi_mean, STDDEV(bsi) AS bsi_std,
                        AVG(ndwi) AS ndwi_mean, STDDEV(ndwi) AS ndwi_std
                    FROM (SELECT * FROM monthly ORDER BY month_dt ASC LIMIT 12) b
                )
                SELECT 
                    TO_CHAR(m.month_dt, 'YYYY-MM') AS month,
                    m.ndvi, m.bsi, m.ndwi, m.water_pct,
                    (m.ndvi - b.ndvi_mean) / NULLIF(b.ndvi_std, 0) AS ndvi_z,
                    (m.bsi - b.bsi_mean) / NULLIF(b.bsi_std, 0) AS bsi_z,
                    (m.ndwi - b.ndwi_mean) / NULLIF(b.ndwi_std, 0) AS ndwi_z
                FROM monthly m, baseline b
                WHERE m.month_dt > (SELECT MIN(month_dt) + INTERVAL '12 months' FROM monthly)
                ORDER BY m.month_dt
            """, (kmz_id,))
            rows = cur.fetchall()
    
    alerts_list = []
    for row in rows:
        ndvi_z = float(row[5]) if row[5] is not None else 0
        bsi_z = float(row[6]) if row[6] is not None else 0
        ndwi_z = float(row[7]) if row[7] is not None else 0
        
        max_abs = max(abs(ndvi_z), abs(bsi_z), abs(ndwi_z))
        
        if max_abs > 2.0:
            severity = "high"
        elif max_abs > 1.5:
            severity = "medium"
        elif max_abs > 1.0:
            severity = "low"
        else:
            continue
        
        alerts_list.append({
            "month": row[0],
            "ndvi": round(float(row[1]), 3) if row[1] else None,
            "bsi": round(float(row[2]), 3) if row[2] else None,
            "ndwi": round(float(row[3]), 3) if row[3] else None,
            "water_pct": round(float(row[4]), 1) if row[4] else None,
            "ndvi_z": round(ndvi_z, 2),
            "bsi_z": round(bsi_z, 2),
            "ndwi_z": round(ndwi_z, 2),
            "severity": severity,
            "max_z": round(max_abs, 2)
        })
    
    return jsonify({
        "kmz_id": kmz_id,
        "alerts": alerts_list,
        "total": len(alerts_list)
    })


@app.route('/api/alerts/<int:kmz_id>/<month>/report')
def generate_report(kmz_id, month):
    """Generează raport oficial via Anthropic Claude."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.name, a.description,
                       ST_Y(ST_Centroid(a.geom)) AS lat,
                       ST_X(ST_Centroid(a.geom)) AS lon,
                       ST_Area(a.geom::geography)/10000 AS area_ha,
                       AVG(o.ndvi_mean) AS ndvi,
                       AVG(o.bsi_mean) AS bsi,
                       AVG(o.ndwi_mean) AS ndwi,
                       AVG(o.water_pixel_pct) AS water_pct,
                       COUNT(o.id) AS n_obs
                FROM kmz_aois a
                LEFT JOIN kmz_observations o ON o.kmz_id = a.id
                    AND TO_CHAR(date_trunc('month', o.observed_at), 'YYYY-MM') = %s
                WHERE a.id = %s
                GROUP BY a.id
            """, (month, kmz_id))
            row = cur.fetchone()
    
    if not row or not row[5]:
        return jsonify({"error": "No data for this month"}), 404
    
    name, description, lat, lon, area_ha, ndvi, bsi, ndwi, water_pct, n_obs = row
    
    prompt = f"""Generează un raport oficial scurt (maxim 400 cuvinte) pentru Garda de Mediu Romania privind detectia automata a unei posibile perturbari antropice intr-un sit Natura 2000.

Date de input (din date satelitare Sentinel-2 / Programul Copernicus al UE):
- Sit: ROSCI0434 - Siretul Mijlociu (zona Natura 2000, directiva Habitate)
- Locație studiată: {name} ({description})
- Coordonate centrale: {lat:.5f}°N, {lon:.5f}°E
- Suprafață monitorizată: {area_ha:.1f} hectare
- Perioada: {month}
- Observatii satelitare in luna: {n_obs}
- Indicatori medii masurati:
  * NDVI (vegetatie): {ndvi:.3f}
  * BSI (sol expus): {bsi:.3f}
  * NDWI (apa): {ndwi:.3f}
  * Procent suprafata apa: {water_pct:.1f}%

Reguli stricte:
1. Limbaj formal de raport oficial.
2. Nu afirma certitudini despre activitate ilegala. Foloseste "se observa", "indicatorii sugereaza", "este recomandata verificarea".
3. Mentioneaza explicit ca datele provin din Programul Copernicus al UE (Sentinel-2).
4. Recomanda verificare in teren si consultare a datelor cadastrale.
5. Mentioneaza temeiul legal: OUG 57/2007 privind regimul ariilor naturale protejate, Directiva Habitate 92/43/CEE.
6. Format: Antet, Constatari, Interpretare, Recomandari, Temei legal.

Generează raportul:"""
    
    try:
        client = Anthropic(api_key=ANTHROPIC_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        report_text = response.content[0].text
    except Exception as e:
        return jsonify({"error": f"LLM error: {str(e)}"}), 500
    
    return jsonify({
        "report": report_text,
        "metadata": {
            "month": month,
            "location": name,
            "ndvi": round(float(ndvi), 3),
            "bsi": round(float(bsi), 3),
            "ndwi": round(float(ndwi), 3),
            "water_pct": round(float(water_pct), 1),
            "coordinates": [float(lat), float(lon)],
            "area_ha": round(float(area_ha), 1),
            "data_source": "Copernicus Sentinel-2 L2A (Earth Search STAC)",
            "generated_at": datetime.now().isoformat()
        }
    })


@app.route('/api/animation/<int:kmz_id>')
def animation_info(kmz_id):
    """Info animație - URL la GIF."""
    gif_path = f"{OUTPUT_DIR}/evolution_{kmz_id}.gif"
    exists = os.path.exists(gif_path)
    return jsonify({
        "kmz_id": kmz_id,
        "exists": exists,
        "url": f"/kmz/evolution_{kmz_id}.gif" if exists else None,
        "filename": os.path.basename(gif_path)
    })


@app.route('/api/scene-images/<int:kmz_id>')
def scene_images(kmz_id):
    """List of all scene images with metadata for gallery."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    observed_at,
                    scene_id,
                    cloud_cover,
                    ndvi_mean,
                    bsi_mean,
                    ndwi_mean,
                    water_pixel_pct,
                    rgb_path,
                    ndvi_path,
                    ndwi_path
                FROM kmz_observations
                WHERE kmz_id = %s AND rgb_path IS NOT NULL
                ORDER BY observed_at ASC
            """, (kmz_id,))
            rows = cur.fetchall()
    
    return jsonify([
        {
            "date": row[0].strftime("%Y-%m-%d"),
            "month": row[0].strftime("%Y-%m"),
            "scene_id": row[1],
            "cloud_cover": round(float(row[2]), 1) if row[2] else None,
            "ndvi": round(float(row[3]), 3) if row[3] else None,
            "bsi": round(float(row[4]), 3) if row[4] else None,
            "ndwi": round(float(row[5]), 3) if row[5] else None,
            "water_pct": round(float(row[6]), 1) if row[6] else None,
            "rgb_url": f"/kmz/{os.path.basename(row[7])}" if row[7] else None,
            "ndvi_url": f"/kmz/{os.path.basename(row[8])}" if row[8] else None,
            "ndwi_url": f"/kmz/{os.path.basename(row[9])}" if row[9] else None,
        }
        for row in rows
    ])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
