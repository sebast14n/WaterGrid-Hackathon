
from celery import Celery
import os

REDIS_URL = os.getenv("REDIS_URL")

app = Celery("siret", broker=REDIS_URL)


@app.task
def test_task(x, y):
    return x + y


@app.task
def compute_dummy_ndvi():
    import numpy as np

    red = np.random.rand(100, 100)
    nir = np.random.rand(100, 100)

    ndvi = (nir - red) / (nir + red + 1e-6)

    return float(ndvi.mean())


@app.task
def create_processing_run(message="worker db test"):
    import psycopg

    db_url = os.getenv("DATABASE_URL")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO processing_runs (run_type, status, message)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                ("test", "finished", message),
            )
            run_id = cur.fetchone()[0]
            conn.commit()

    return run_id


@app.task
def search_sentinel2_scenes(days_back=30, max_cloud=30):
    import json
    import requests
    import psycopg
    from datetime import datetime, timedelta, timezone

    db_url = os.getenv("DATABASE_URL")
    stac_url = "https://earth-search.aws.element84.com/v1/search"

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(
                        ST_RemoveRepeatedPoints(geom, 0.0000001)
                    )
                )
                FROM aoi
                WHERE id = 1
            """)
            row = cur.fetchone()

    if not row:
        raise RuntimeError("AOI id=1 not found")

    aoi_geojson = json.loads(row[0])

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    payload = {
        "collections": ["sentinel-2-l2a"],
        "intersects": aoi_geojson,
        "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}",
        "limit": 10,
        "query": {
            "eo:cloud_cover": {
                "lt": max_cloud
            }
        },
        "sortby": [
            {
                "field": "properties.datetime",
                "direction": "desc"
            }
        ]
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "siret-monitor/0.1"
    }

    r = requests.post(stac_url, json=payload, headers=headers, timeout=60)

    if r.status_code != 200:
        raise RuntimeError(
            f"STAC HTTP {r.status_code}; "
            f"content-type={r.headers.get('content-type')}; "
            f"body={r.text[:500]}"
        )

    data = r.json()
    features = data.get("features", [])

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processing_runs (run_type, status, message)
                VALUES (%s, %s, %s)
            """, (
                "sentinel2_search",
                "finished",
                f"Found {len(features)} Sentinel-2 L2A scenes via Earth Search in last {days_back} days"
            ))
            conn.commit()

    return [
        {
            "id": f.get("id"),
            "datetime": f.get("properties", {}).get("datetime"),
            "cloud": f.get("properties", {}).get("eo:cloud_cover"),
        }
        for f in features
    ]


@app.task
def process_latest_sentinel2_indices(days_back=30, max_cloud=40):
    import json
    import requests
    import numpy as np
    import psycopg
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import transform_geom, reproject, Resampling
    from datetime import datetime, timedelta, timezone

    db_url = os.getenv("DATABASE_URL")
    stac_url = "https://earth-search.aws.element84.com/v1/search"

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(
                        ST_RemoveRepeatedPoints(geom, 0.0000001)
                    )
                )
                FROM aoi
                WHERE id = 1
            """)
            row = cur.fetchone()

    if not row:
        raise RuntimeError("AOI id=1 not found")

    aoi_geojson = json.loads(row[0])

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    payload = {
        "collections": ["sentinel-2-l2a"],
        "intersects": aoi_geojson,
        "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}",
        "limit": 5,
        "query": {
            "eo:cloud_cover": {
                "lt": max_cloud
            }
        },
        "sortby": [
            {
                "field": "properties.datetime",
                "direction": "desc"
            }
        ]
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "siret-monitor/0.1"
    }

    r = requests.post(stac_url, json=payload, headers=headers, timeout=60)

    if r.status_code != 200:
        raise RuntimeError(
            f"STAC HTTP {r.status_code}; "
            f"content-type={r.headers.get('content-type')}; "
            f"body={r.text[:500]}"
        )

    features = r.json().get("features", [])

    if not features:
        raise RuntimeError(
            f"No Sentinel-2 scenes found for AOI in last {days_back} days with cloud < {max_cloud}"
        )

    scene = features[0]
    scene_id = scene["id"]
    props = scene.get("properties", {})
    observed_at = props.get("datetime")
    cloud_cover = props.get("eo:cloud_cover")

    assets = scene.get("assets", {})

    required_assets = ["blue", "red", "nir", "swir16"]
    missing = [key for key in required_assets if key not in assets]

    if missing:
        raise RuntimeError(
            f"Missing assets in scene {scene_id}: {missing}. "
            f"Available assets: {list(assets.keys())}"
        )

    href_blue = assets["blue"]["href"]
    href_red = assets["red"]["href"]
    href_nir = assets["nir"]["href"]
    href_swir16 = assets["swir16"]["href"]

    def raster_env():
        return rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF"
        )

    def read_reference_band(href, geom_wgs84):
        with raster_env():
            with rasterio.open(href) as src:
                geom_src_crs = transform_geom(
                    "EPSG:4326",
                    src.crs.to_string(),
                    geom_wgs84,
                    precision=6
                )

                data, transform = mask(
                    src,
                    [geom_src_crs],
                    crop=True,
                    filled=False
                )

                arr = data[0].astype("float32")

                if src.nodata is not None:
                    arr = np.ma.masked_equal(arr, src.nodata)

                arr = np.ma.masked_where(arr <= 0, arr)

                return arr, transform, src.crs

    def read_band_resampled_to_reference(href, ref_shape, ref_transform, ref_crs):
        with raster_env():
            with rasterio.open(href) as src:
                dst = np.empty(ref_shape, dtype="float32")

                reproject(
                    source=rasterio.band(src, 1),
                    destination=dst,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=ref_transform,
                    dst_crs=ref_crs,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear
                )

                arr = np.ma.masked_invalid(dst)
                arr = np.ma.masked_where(arr <= 0, arr)

                return arr

    red, ref_transform, ref_crs = read_reference_band(href_red, aoi_geojson)
    blue = read_band_resampled_to_reference(href_blue, red.shape, ref_transform, ref_crs)
    nir = read_band_resampled_to_reference(href_nir, red.shape, ref_transform, ref_crs)
    swir16 = read_band_resampled_to_reference(href_swir16, red.shape, ref_transform, ref_crs)

    ndvi = (nir - red) / (nir + red + 1e-6)
    bsi = ((swir16 + red) - (nir + blue)) / ((swir16 + red) + (nir + blue) + 1e-6)

    ndvi_mean = float(np.ma.mean(ndvi))
    bsi_mean = float(np.ma.mean(bsi))
    pixel_count = int(np.ma.count(ndvi))

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO satellite_observations (
                    aoi_id,
                    scene_id,
                    observed_at,
                    cloud_cover,
                    ndvi_mean,
                    bsi_mean,
                    pixel_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (aoi_id, scene_id)
                DO UPDATE SET
                    cloud_cover = EXCLUDED.cloud_cover,
                    ndvi_mean = EXCLUDED.ndvi_mean,
                    bsi_mean = EXCLUDED.bsi_mean,
                    pixel_count = EXCLUDED.pixel_count
                RETURNING id
            """, (
                1,
                scene_id,
                observed_at,
                cloud_cover,
                ndvi_mean,
                bsi_mean,
                pixel_count
            ))

            obs_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO processing_runs (run_type, status, message)
                VALUES (%s, %s, %s)
            """, (
                "sentinel2_indices",
                "finished",
                f"Processed {scene_id}: NDVI={ndvi_mean:.4f}, BSI={bsi_mean:.4f}, pixels={pixel_count}"
            ))

            conn.commit()

    return {
        "observation_id": obs_id,
        "scene_id": scene_id,
        "observed_at": observed_at,
        "cloud_cover": cloud_cover,
        "ndvi_mean": ndvi_mean,
        "bsi_mean": bsi_mean,
        "pixel_count": pixel_count
    }

@app.task
def process_sentinel2_timeseries(days_back=365, max_cloud=50, limit=50):
    import json
    import requests
    import numpy as np
    import psycopg
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import transform_geom, reproject, Resampling
    from datetime import datetime, timedelta, timezone

    db_url = os.getenv("DATABASE_URL")
    stac_url = "https://earth-search.aws.element84.com/v1/search"

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(
                        ST_RemoveRepeatedPoints(geom, 0.0000001)
                    )
                )
                FROM aoi
                WHERE id = 1
            """)
            row = cur.fetchone()

    if not row:
        raise RuntimeError("AOI id=1 not found")

    aoi_geojson = json.loads(row[0])

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    payload = {
        "collections": ["sentinel-2-l2a"],
        "intersects": aoi_geojson,
        "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}",
        "limit": limit,
        "query": {
            "eo:cloud_cover": {
                "lt": max_cloud
            }
        },
        "sortby": [
            {
                "field": "properties.datetime",
                "direction": "asc"
            }
        ]
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "siret-monitor/0.1"
    }

    r = requests.post(stac_url, json=payload, headers=headers, timeout=60)

    if r.status_code != 200:
        raise RuntimeError(
            f"STAC HTTP {r.status_code}; "
            f"content-type={r.headers.get('content-type')}; "
            f"body={r.text[:500]}"
        )

    features = r.json().get("features", [])

    if not features:
        raise RuntimeError(
            f"No Sentinel-2 scenes found for AOI in last {days_back} days with cloud < {max_cloud}"
        )

    def raster_env():
        return rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF"
        )

    def read_reference_band(href, geom_wgs84):
        with raster_env():
            with rasterio.open(href) as src:
                geom_src_crs = transform_geom(
                    "EPSG:4326",
                    src.crs.to_string(),
                    geom_wgs84,
                    precision=6
                )

                data, transform = mask(
                    src,
                    [geom_src_crs],
                    crop=True,
                    filled=False
                )

                arr = data[0].astype("float32")

                if src.nodata is not None:
                    arr = np.ma.masked_equal(arr, src.nodata)

                arr = np.ma.masked_where(arr <= 0, arr)

                return arr, transform, src.crs

    def read_band_resampled_to_reference(href, ref_shape, ref_transform, ref_crs):
        with raster_env():
            with rasterio.open(href) as src:
                dst = np.empty(ref_shape, dtype="float32")

                reproject(
                    source=rasterio.band(src, 1),
                    destination=dst,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=ref_transform,
                    dst_crs=ref_crs,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear
                )

                arr = np.ma.masked_invalid(dst)
                arr = np.ma.masked_where(arr <= 0, arr)

                return arr

    processed = []
    failed = []

    for scene in features:
        scene_id = scene["id"]
        props = scene.get("properties", {})
        observed_at = props.get("datetime")
        cloud_cover = props.get("eo:cloud_cover")
        assets = scene.get("assets", {})

        try:
            required_assets = ["blue", "red", "nir", "swir16"]
            missing = [key for key in required_assets if key not in assets]

            if missing:
                raise RuntimeError(
                    f"Missing assets {missing}; available={list(assets.keys())}"
                )

            href_blue = assets["blue"]["href"]
            href_red = assets["red"]["href"]
            href_nir = assets["nir"]["href"]
            href_swir16 = assets["swir16"]["href"]

            red, ref_transform, ref_crs = read_reference_band(href_red, aoi_geojson)
            blue = read_band_resampled_to_reference(href_blue, red.shape, ref_transform, ref_crs)
            nir = read_band_resampled_to_reference(href_nir, red.shape, ref_transform, ref_crs)
            swir16 = read_band_resampled_to_reference(href_swir16, red.shape, ref_transform, ref_crs)

            ndvi = (nir - red) / (nir + red + 1e-6)
            bsi = ((swir16 + red) - (nir + blue)) / ((swir16 + red) + (nir + blue) + 1e-6)

            ndvi_mean = float(np.ma.mean(ndvi))
            bsi_mean = float(np.ma.mean(bsi))
            pixel_count = int(np.ma.count(ndvi))

            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO satellite_observations (
                            aoi_id,
                            scene_id,
                            observed_at,
                            cloud_cover,
                            ndvi_mean,
                            bsi_mean,
                            pixel_count
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (aoi_id, scene_id)
                        DO UPDATE SET
                            observed_at = EXCLUDED.observed_at,
                            cloud_cover = EXCLUDED.cloud_cover,
                            ndvi_mean = EXCLUDED.ndvi_mean,
                            bsi_mean = EXCLUDED.bsi_mean,
                            pixel_count = EXCLUDED.pixel_count
                        RETURNING id
                    """, (
                        1,
                        scene_id,
                        observed_at,
                        cloud_cover,
                        ndvi_mean,
                        bsi_mean,
                        pixel_count
                    ))

                    obs_id = cur.fetchone()[0]
                    conn.commit()

            processed.append({
                "observation_id": obs_id,
                "scene_id": scene_id,
                "observed_at": observed_at,
                "cloud_cover": cloud_cover,
                "ndvi_mean": ndvi_mean,
                "bsi_mean": bsi_mean,
                "pixel_count": pixel_count
            })

        except Exception as exc:
            failed.append({
                "scene_id": scene_id,
                "error": str(exc)[:500]
            })

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processing_runs (run_type, status, message)
                VALUES (%s, %s, %s)
            """, (
                "sentinel2_timeseries",
                "finished" if processed else "failed",
                f"Processed {len(processed)} scenes, failed {len(failed)} scenes, days_back={days_back}, max_cloud={max_cloud}"
            ))
            conn.commit()

    return {
        "processed_count": len(processed),
        "failed_count": len(failed),
        "processed": processed,
        "failed": failed
    }


@app.task
def process_kmz_scene(kmz_id, scene_id, scene_assets_json, observed_at, cloud_cover, output_dir="/data/kmz_outputs"):
    """
    Procesează O SINGURĂ scenă pentru o zonă KMZ.
    Distribuibil — fiecare worker poate prinde o scenă diferită.
    """
    import json
    import os
    import numpy as np
    import psycopg
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import transform_geom, reproject, Resampling
    from PIL import Image, ImageDraw
    import matplotlib.cm as cm

    os.makedirs(output_dir, exist_ok=True)
    db_url = os.getenv("DATABASE_URL")
    assets = json.loads(scene_assets_json)

    # Ia geometria KMZ din DB
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(ST_RemoveRepeatedPoints(geom, 0.0000001))
                )
                FROM kmz_aois WHERE id = %s
            """, (kmz_id,))
            row = cur.fetchone()

    if not row:
        raise RuntimeError(f"KMZ id={kmz_id} not found")

    kmz_geom = json.loads(row[0])

    def raster_env():
        return rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF"
        )

    def read_reference_band(href, geom_wgs84):
        with raster_env():
            with rasterio.open(href) as src:
                geom_src = transform_geom("EPSG:4326", src.crs.to_string(), geom_wgs84, precision=6)
                data, transform = mask(src, [geom_src], crop=True, filled=False)
                arr = data[0].astype("float32")
                if src.nodata is not None:
                    arr = np.ma.masked_equal(arr, src.nodata)
                arr = np.ma.masked_where(arr <= 0, arr)
                return arr, transform, src.crs

    def read_band_resampled(href, ref_shape, ref_transform, ref_crs):
        with raster_env():
            with rasterio.open(href) as src:
                dst = np.empty(ref_shape, dtype="float32")
                reproject(
                    source=rasterio.band(src, 1),
                    destination=dst,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=ref_transform,
                    dst_crs=ref_crs,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear
                )
                arr = np.ma.masked_invalid(dst)
                arr = np.ma.masked_where(arr <= 0, arr)
                return arr

    def normalize_for_display(arr, lo=None, hi=None):
        a = arr.filled(0).astype(np.float32) if hasattr(arr, 'filled') else arr.astype(np.float32)
        valid = a[a > 0]
        if len(valid) == 0:
            return np.zeros(a.shape, dtype=np.uint8)
        if lo is None:
            lo = float(np.percentile(valid, 2))
        if hi is None:
            hi = float(np.percentile(valid, 98))
        a = np.clip((a - lo) / (hi - lo + 1e-6), 0, 1)
        return (a * 255).astype(np.uint8)

    try:
        red, ref_transform, ref_crs = read_reference_band(assets["red"], kmz_geom)
        green = read_band_resampled(assets["green"], red.shape, ref_transform, ref_crs)
        blue = read_band_resampled(assets["blue"], red.shape, ref_transform, ref_crs)
        nir = read_band_resampled(assets["nir"], red.shape, ref_transform, ref_crs)
        swir = read_band_resampled(assets["swir16"], red.shape, ref_transform, ref_crs)

        ndvi = (nir - red) / (nir + red + 1e-6)
        bsi = ((swir + red) - (nir + blue)) / ((swir + red) + (nir + blue) + 1e-6)

        ndvi_mean = float(np.ma.mean(ndvi))
        bsi_mean = float(np.ma.mean(bsi))
        pixel_count = int(np.ma.count(ndvi))

        # Skip dacă pixeli prea puțini
        if pixel_count < 100:
            return {"scene_id": scene_id, "status": "skipped_low_pixels", "pixel_count": pixel_count}

        # Salvează RGB PNG
        date_str = observed_at[:10]
        rgb_path = f"{output_dir}/kmz{kmz_id}_{date_str}_{scene_id}_rgb.png"
        ndvi_path = f"{output_dir}/kmz{kmz_id}_{date_str}_{scene_id}_ndvi.png"

        r = normalize_for_display(red)
        g = normalize_for_display(green)
        b = normalize_for_display(blue)
        rgb = np.stack([r, g, b], axis=-1)

        # Upscale 4x pentru imagini mai mari în UI
        img_rgb = Image.fromarray(rgb)
        new_size = (rgb.shape[1] * 4, rgb.shape[0] * 4)
        img_rgb = img_rgb.resize(new_size, Image.NEAREST)
        img_rgb.save(rgb_path)

        # NDVI colorized
        ndvi_arr = ndvi.filled(0) if hasattr(ndvi, 'filled') else ndvi
        ndvi_norm = np.clip((ndvi_arr + 1) / 2, 0, 1)
        cmap = cm.get_cmap('RdYlGn')
        colored = (cmap(ndvi_norm)[:, :, :3] * 255).astype(np.uint8)
        img_ndvi = Image.fromarray(colored).resize(new_size, Image.NEAREST)
        img_ndvi.save(ndvi_path)

        # Salvează în DB
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kmz_observations
                        (kmz_id, scene_id, observed_at, cloud_cover,
                         ndvi_mean, bsi_mean, pixel_count, rgb_path, ndvi_path)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (kmz_id, scene_id) DO UPDATE SET
                        observed_at = EXCLUDED.observed_at,
                        cloud_cover = EXCLUDED.cloud_cover,
                        ndvi_mean = EXCLUDED.ndvi_mean,
                        bsi_mean = EXCLUDED.bsi_mean,
                        pixel_count = EXCLUDED.pixel_count,
                        rgb_path = EXCLUDED.rgb_path,
                        ndvi_path = EXCLUDED.ndvi_path
                """, (kmz_id, scene_id, observed_at, cloud_cover,
                      ndvi_mean, bsi_mean, pixel_count, rgb_path, ndvi_path))
                conn.commit()

        return {
            "scene_id": scene_id,
            "status": "ok",
            "ndvi": ndvi_mean,
            "bsi": bsi_mean,
            "pixel_count": pixel_count,
            "rgb_path": rgb_path
        }

    except Exception as e:
        return {"scene_id": scene_id, "status": "error", "error": str(e)[:500]}


@app.task
def search_and_dispatch_kmz(kmz_id, days_back=730, max_cloud=80):
    """
    Caută toate scenele Sentinel-2 pentru zona KMZ și dispecerizează
    procesarea per scenă (paralelizare reală pe fsn1+fsn2).
    """
    import json
    import requests
    import psycopg
    from datetime import datetime, timedelta, timezone

    db_url = os.getenv("DATABASE_URL")
    stac_url = "https://earth-search.aws.element84.com/v1/search"

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(ST_RemoveRepeatedPoints(geom, 0.0000001))
                )
                FROM kmz_aois WHERE id = %s
            """, (kmz_id,))
            row = cur.fetchone()

    if not row:
        raise RuntimeError(f"KMZ id={kmz_id} not found")

    kmz_geom = json.loads(row[0])

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    payload = {
        "collections": ["sentinel-2-l2a"],
        "intersects": kmz_geom,
        "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}",
        "limit": 500,
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "asc"}]
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "siret-monitor/0.1"
    }

    r = requests.post(stac_url, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    features = r.json().get("features", [])

    dispatched = []
    for scene in features:
        scene_id = scene["id"]
        props = scene.get("properties", {})
        observed_at = props.get("datetime")
        cloud_cover = props.get("eo:cloud_cover")
        assets_raw = scene.get("assets", {})

        required = ["red", "green", "blue", "nir", "swir16"]
        if not all(k in assets_raw for k in required):
            continue

        assets = {k: assets_raw[k]["href"] for k in required}

        # Dispecerizează ca task separat — Celery îl va da la fsn1 sau fsn2
        result = process_kmz_scene.delay(
            kmz_id=kmz_id,
            scene_id=scene_id,
            scene_assets_json=json.dumps(assets),
            observed_at=observed_at,
            cloud_cover=cloud_cover
        )
        dispatched.append({"scene_id": scene_id, "task_id": result.id})

    return {
        "total_scenes_found": len(features),
        "dispatched": len(dispatched),
        "kmz_id": kmz_id
    }


@app.task
def generate_monthly_animation(kmz_id=1, output_path=None, duration_ms=600):
    """
    Generează GIF din imaginile RGB lunare ale zonei KMZ.
    Pentru fiecare lună, ia scena cu pixel_count maxim.
    """
    import psycopg
    import os
    from PIL import Image, ImageDraw, ImageFont

    if output_path is None:
        output_path = f"/data/kmz_outputs/evolution_{kmz_id}.gif"

    db_url = os.getenv("DATABASE_URL")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (date_trunc('month', observed_at))
                    observed_at, rgb_path, ndvi_mean, bsi_mean
                FROM kmz_observations
                WHERE kmz_id = %s 
                  AND rgb_path IS NOT NULL 
                  AND pixel_count > 100
                ORDER BY date_trunc('month', observed_at), pixel_count DESC
            """, (kmz_id,))
            rows = cur.fetchall()

    if not rows:
        return {"error": "No images found", "output": None}

    frames = []
    for observed_at, rgb_path, ndvi, bsi in rows:
        if not os.path.exists(rgb_path):
            continue
        try:
            img = Image.open(rgb_path).convert("RGB")
            draw = ImageDraw.Draw(img)
            
            # Banner negru jos cu informații
            w, h = img.size
            banner_h = 40
            draw.rectangle([(0, h - banner_h), (w, h)], fill=(0, 0, 0))
            
            label = f"{observed_at.strftime('%Y-%m-%d')}  NDVI={ndvi:.2f}  BSI={bsi:.2f}"
            
            # Folosește fontul default
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            except:
                font = ImageFont.load_default()
            
            draw.text((10, h - banner_h + 10), label, fill="white", font=font)
            frames.append(img)
        except Exception as e:
            print(f"Skipped {rgb_path}: {e}")

    if not frames:
        return {"error": "No frames generated", "output": None}

    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True
    )

    return {
        "frames": len(frames),
        "output": output_path,
        "kmz_id": kmz_id
    }


@app.task
def compute_ndwi_for_scene(kmz_id, scene_id, scene_assets_json, output_dir="/data/kmz_outputs"):
    """
    Calculează DOAR NDWI pentru o scenă deja procesată.
    Nu atinge NDVI/BSI/RGB existente.
    """
    import json
    import os
    import numpy as np
    import psycopg
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import transform_geom, reproject, Resampling
    from PIL import Image
    import matplotlib.cm as cm

    db_url = os.getenv("DATABASE_URL")
    assets = json.loads(scene_assets_json)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(ST_RemoveRepeatedPoints(geom, 0.0000001))
                )
                FROM kmz_aois WHERE id = %s
            """, (kmz_id,))
            row = cur.fetchone()

    if not row:
        raise RuntimeError(f"KMZ id={kmz_id} not found")

    kmz_geom = json.loads(row[0])

    def raster_env():
        return rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF"
        )

    def read_reference_band(href, geom_wgs84):
        with raster_env():
            with rasterio.open(href) as src:
                geom_src = transform_geom("EPSG:4326", src.crs.to_string(), geom_wgs84, precision=6)
                data, transform = mask(src, [geom_src], crop=True, filled=False)
                arr = data[0].astype("float32")
                if src.nodata is not None:
                    arr = np.ma.masked_equal(arr, src.nodata)
                arr = np.ma.masked_where(arr <= 0, arr)
                return arr, transform, src.crs

    def read_band_resampled(href, ref_shape, ref_transform, ref_crs):
        with raster_env():
            with rasterio.open(href) as src:
                dst = np.empty(ref_shape, dtype="float32")
                reproject(
                    source=rasterio.band(src, 1),
                    destination=dst,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=ref_transform,
                    dst_crs=ref_crs,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear
                )
                arr = np.ma.masked_invalid(dst)
                arr = np.ma.masked_where(arr <= 0, arr)
                return arr

    try:
        # Citim doar Green și NIR (suficient pentru NDWI)
        green, ref_transform, ref_crs = read_reference_band(assets["green"], kmz_geom)
        nir = read_band_resampled(assets["nir"], green.shape, ref_transform, ref_crs)

        ndwi = (green - nir) / (green + nir + 1e-6)
        ndwi_mean = float(np.ma.mean(ndwi))
        pixel_count = int(np.ma.count(ndwi))

        if pixel_count < 100:
            return {"scene_id": scene_id, "status": "skipped", "pixel_count": pixel_count}

        # Procent de pixeli care sunt apa (NDWI > 0.3)
        water_mask = ndwi > 0.3
        water_count = int(np.ma.sum(water_mask))
        water_pixel_pct = float(water_count / pixel_count * 100) if pixel_count > 0 else 0

        # Salvează NDWI PNG
        scene_id_safe = scene_id.replace("/", "_")
        # Găsește data din DB
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT observed_at::date FROM kmz_observations
                    WHERE kmz_id = %s AND scene_id = %s
                """, (kmz_id, scene_id))
                date_row = cur.fetchone()

        if date_row:
            date_str = date_row[0].strftime("%Y-%m-%d")
            ndwi_path = f"{output_dir}/kmz{kmz_id}_{date_str}_{scene_id_safe}_ndwi.png"

            ndwi_arr = ndwi.filled(0) if hasattr(ndwi, 'filled') else ndwi
            ndwi_norm = np.clip((ndwi_arr + 1) / 2, 0, 1)
            cmap_water = cm.get_cmap('Blues')
            colored_w = (cmap_water(ndwi_norm)[:, :, :3] * 255).astype(np.uint8)
            
            new_size = (ndwi_arr.shape[1] * 4, ndwi_arr.shape[0] * 4)
            img_ndwi = Image.fromarray(colored_w).resize(new_size, Image.NEAREST)
            img_ndwi.save(ndwi_path)
        else:
            ndwi_path = None

        # UPDATE doar coloanele NDWI, fără să atingem celelalte
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE kmz_observations 
                    SET ndwi_mean = %s,
                        water_pixel_pct = %s,
                        ndwi_path = %s
                    WHERE kmz_id = %s AND scene_id = %s
                """, (ndwi_mean, water_pixel_pct, ndwi_path, kmz_id, scene_id))
                conn.commit()

        return {
            "scene_id": scene_id,
            "status": "ok",
            "ndwi": ndwi_mean,
            "water_pct": water_pixel_pct,
            "pixel_count": pixel_count
        }

    except Exception as e:
        return {"scene_id": scene_id, "status": "error", "error": str(e)[:500]}


@app.task
def dispatch_ndwi_for_existing_scenes(kmz_id=1, days_back=730, max_cloud=80):
    """
    Re-caută scenele și dispecerizează calcul NDWI pentru fiecare.
    Nu re-creează observații, doar updatează cele existente cu NDWI.
    """
    import json
    import requests
    import psycopg
    from datetime import datetime, timedelta, timezone

    db_url = os.getenv("DATABASE_URL")
    stac_url = "https://earth-search.aws.element84.com/v1/search"

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(ST_RemoveRepeatedPoints(geom, 0.0000001))
                )
                FROM kmz_aois WHERE id = %s
            """, (kmz_id,))
            row = cur.fetchone()

    if not row:
        raise RuntimeError(f"KMZ id={kmz_id} not found")

    kmz_geom = json.loads(row[0])

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    payload = {
        "collections": ["sentinel-2-l2a"],
        "intersects": kmz_geom,
        "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}",
        "limit": 500,
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "asc"}]
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "siret-monitor/0.1"
    }

    r = requests.post(stac_url, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    features = r.json().get("features", [])

    dispatched = 0
    for scene in features:
        scene_id = scene["id"]
        assets_raw = scene.get("assets", {})

        if "green" not in assets_raw or "nir" not in assets_raw:
            continue

        # Verifică dacă scena există deja în DB
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM kmz_observations 
                    WHERE kmz_id = %s AND scene_id = %s AND ndwi_mean IS NULL
                """, (kmz_id, scene_id))
                needs_ndwi = cur.fetchone() is not None

        if not needs_ndwi:
            continue

        assets = {
            "green": assets_raw["green"]["href"],
            "nir": assets_raw["nir"]["href"]
        }

        compute_ndwi_for_scene.delay(
            kmz_id=kmz_id,
            scene_id=scene_id,
            scene_assets_json=json.dumps(assets)
        )
        dispatched += 1

    return {"dispatched": dispatched, "total_scenes_found": len(features)}


@app.task
def process_kmz_scene_advanced(kmz_id, scene_assets_json, observed_at, output_dir="/data/kmz_outputs"):
    """
    Advanced processing with SCL cloud masking and plastic index.
    Innovation: Professional-grade pixel masking before analysis.
    """
    import json
    import os
    import numpy as np
    import psycopg
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import transform_geom, reproject, Resampling
    from PIL import Image
    import matplotlib.cm as cm
    from datetime import datetime
    
    # Import our cloud masking module
    from app.cloud_masking import apply_scl_mask, compute_indices_masked, aggregate_with_nan_handling

    db_url = os.getenv("DATABASE_URL")
    assets = json.loads(scene_assets_json)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ST_AsGeoJSON(
                    ST_MakeValid(ST_RemoveRepeatedPoints(geom, 0.0000001))
                )
                FROM kmz_aois WHERE id = %s
            """, (kmz_id,))
            row = cur.fetchone()

    if not row:
        raise RuntimeError(f"KMZ id={kmz_id} not found")

    kmz_geom = json.loads(row[0])
    scene_id = assets.get('scene_id', 'unknown')

    def raster_env():
        return rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF"
        )

    def read_band_clipped(href, geom_wgs84):
        """Read and clip a single band to AOI"""
        with raster_env():
            with rasterio.open(href) as src:
                geom_src = transform_geom("EPSG:4326", src.crs.to_string(), geom_wgs84, precision=6)
                data, transform = mask(src, [geom_src], crop=True, filled=False)
                arr = data[0].astype("float32")
                if src.nodata is not None:
                    arr = np.ma.masked_equal(arr, src.nodata)
                arr = np.ma.masked_where(arr <= 0, arr)
                return arr, transform, src.crs

    try:
        # Read all required bands including SCL
        blue, ref_transform, ref_crs = read_band_clipped(assets["blue"], kmz_geom)
        green = read_band_clipped(assets["green"], kmz_geom)[0]
        red = read_band_clipped(assets["red"], kmz_geom)[0] 
        nir = read_band_clipped(assets["nir"], kmz_geom)[0]
        swir = read_band_clipped(assets["swir16"], kmz_geom)[0]
        scl = read_band_clipped(assets["scl"], kmz_geom)[0]

        # Prepare bands dict for masking
        bands_data = {
            'blue': blue.filled(0) if hasattr(blue, 'filled') else blue,
            'green': green.filled(0) if hasattr(green, 'filled') else green,
            'red': red.filled(0) if hasattr(red, 'filled') else red,
            'nir': nir.filled(0) if hasattr(nir, 'filled') else nir,
            'swir': swir.filled(0) if hasattr(swir, 'filled') else swir
        }
        
        scl_data = scl.filled(0) if hasattr(scl, 'filled') else scl

        # Apply advanced cloud masking
        masked_bands, valid_pixel_ratio = apply_scl_mask(bands_data, scl_data)
        
        if valid_pixel_ratio < 0.1:  # Less than 10% valid pixels
            return {
                "scene_id": scene_id,
                "status": "skipped_cloudy", 
                "valid_pixel_ratio": valid_pixel_ratio,
                "reason": "Too few valid pixels after cloud masking"
            }

        # Compute indices on masked data
        indices = compute_indices_masked(masked_bands)
        
        # Aggregate with NaN handling
        ndvi_mean, ndvi_count = aggregate_with_nan_handling(indices['ndvi'])
        bsi_mean, bsi_count = aggregate_with_nan_handling(indices['bsi'])
        ndwi_mean, ndwi_count = aggregate_with_nan_handling(indices['ndwi'])
        pi_mean, pi_count = aggregate_with_nan_handling(indices['plastic_index'])
        
        # Water percentage calculation (NDWI > 0.3)
        water_mask = indices['ndwi'] > 0.3
        water_pixels = np.sum(np.isfinite(indices['ndwi']) & water_mask)
        total_valid = np.sum(np.isfinite(indices['ndwi']))
        water_pixel_pct = float(water_pixels / total_valid * 100) if total_valid > 0 else 0

        # Generate enhanced visualizations
        date_str = observed_at.strftime("%Y-%m-%d") if isinstance(observed_at, datetime) else str(observed_at)[:10]
        scene_id_safe = scene_id.replace("/", "_")
        
        # RGB composite (with cloud masking)
        rgb_path = f"{output_dir}/kmz{kmz_id}_{date_str}_{scene_id_safe}_rgb_masked.png"
        rgb_masked = np.stack([
            np.clip(masked_bands['red']/10000 * 2.5, 0, 1),
            np.clip(masked_bands['green']/10000 * 2.5, 0, 1), 
            np.clip(masked_bands['blue']/10000 * 2.5, 0, 1)
        ], axis=2)
        rgb_masked = np.where(np.isnan(rgb_masked), 0, rgb_masked)
        rgb_img = (rgb_masked * 255).astype(np.uint8)
        Image.fromarray(rgb_img).save(rgb_path)

        # NDVI colorized
        ndvi_path = f"{output_dir}/kmz{kmz_id}_{date_str}_{scene_id_safe}_ndvi_masked.png"
        ndvi_normalized = np.clip((indices['ndvi'] + 1) / 2, 0, 1)
        ndvi_colored = (cm.RdYlGn(ndvi_normalized)[:, :, :3] * 255).astype(np.uint8)
        Image.fromarray(ndvi_colored).save(ndvi_path)

        # NDWI colorized  
        ndwi_path = f"{output_dir}/kmz{kmz_id}_{date_str}_{scene_id_safe}_ndwi_masked.png"
        ndwi_normalized = np.clip((indices['ndwi'] + 1) / 2, 0, 1)
        ndwi_colored = (cm.Blues(ndwi_normalized)[:, :, :3] * 255).astype(np.uint8)
        Image.fromarray(ndwi_colored).save(ndwi_path)

        # Plastic Index visualization (Innovation!)
        pi_path = f"{output_dir}/kmz{kmz_id}_{date_str}_{scene_id_safe}_plastic.png"
        pi_normalized = np.clip((indices['plastic_index'] + 0.5) / 1.0, 0, 1)
        pi_colored = (cm.plasma(pi_normalized)[:, :, :3] * 255).astype(np.uint8)
        Image.fromarray(pi_colored).save(pi_path)

        # Store in database with advanced metrics
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kmz_observations_advanced
                        (kmz_id, scene_id, observed_at, cloud_cover,
                         ndvi_mean, bsi_mean, ndwi_mean, plastic_index_mean,
                         water_pixel_pct, valid_pixel_ratio, pixel_count_valid,
                         rgb_path, ndvi_path, ndwi_path, plastic_path)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (kmz_id, scene_id) DO UPDATE SET
                        observed_at = EXCLUDED.observed_at,
                        ndvi_mean = EXCLUDED.ndvi_mean,
                        bsi_mean = EXCLUDED.bsi_mean,
                        ndwi_mean = EXCLUDED.ndwi_mean,
                        plastic_index_mean = EXCLUDED.plastic_index_mean,
                        water_pixel_pct = EXCLUDED.water_pixel_pct,
                        valid_pixel_ratio = EXCLUDED.valid_pixel_ratio,
                        pixel_count_valid = EXCLUDED.pixel_count_valid,
                        rgb_path = EXCLUDED.rgb_path,
                        ndvi_path = EXCLUDED.ndvi_path,
                        ndwi_path = EXCLUDED.ndwi_path,
                        plastic_path = EXCLUDED.plastic_path
                """, (
                    kmz_id, scene_id, observed_at, 0,  # cloud_cover placeholder
                    ndvi_mean, bsi_mean, ndwi_mean, pi_mean,
                    water_pixel_pct, valid_pixel_ratio, ndvi_count,
                    rgb_path, ndvi_path, ndwi_path, pi_path
                ))
                conn.commit()

        return {
            "scene_id": scene_id,
            "status": "success",
            "metrics": {
                "ndvi": ndvi_mean,
                "bsi": bsi_mean,
                "ndwi": ndwi_mean,
                "plastic_index": pi_mean,
                "water_pct": water_pixel_pct,
                "valid_pixel_ratio": valid_pixel_ratio,
                "pixel_count": ndvi_count
            },
            "innovation": "Advanced SCL cloud masking + Plastic Index detection"
        }

    except Exception as e:
        return {
            "scene_id": scene_id,
            "status": "error",
            "error": str(e)[:500],
            "innovation_attempted": True
        }
