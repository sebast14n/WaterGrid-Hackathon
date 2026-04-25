CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS aoi (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  geom GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sites (
  id SERIAL PRIMARY KEY,
  geom GEOMETRY(POLYGON, 4326) NOT NULL,
  first_seen DATE,
  last_seen DATE,
  status TEXT DEFAULT 'candidate',
  suspicion_score NUMERIC DEFAULT 0,
  impact_score NUMERIC DEFAULT 0,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS observations (
  id SERIAL PRIMARY KEY,
  site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
  observed_at DATE NOT NULL,
  ndvi NUMERIC,
  ndwi NUMERIC,
  bsi NUMERIC,
  moisture NUMERIC,
  area_m2 NUMERIC,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
  id SERIAL PRIMARY KEY,
  site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
  alert_type TEXT NOT NULL,
  severity INTEGER DEFAULT 1,
  message TEXT,
  created_at TIMESTAMP DEFAULT now(),
  resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processing_runs (
  id SERIAL PRIMARY KEY,
  run_type TEXT NOT NULL,
  status TEXT DEFAULT 'started',
  started_at TIMESTAMP DEFAULT now(),
  finished_at TIMESTAMP,
  message TEXT
);

CREATE INDEX IF NOT EXISTS idx_aoi_geom ON aoi USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_sites_geom ON sites USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_observations_site_date ON observations (site_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_alerts_site ON alerts (site_id);
