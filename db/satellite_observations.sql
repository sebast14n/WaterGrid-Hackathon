CREATE TABLE IF NOT EXISTS satellite_observations (
  id SERIAL PRIMARY KEY,
  aoi_id INTEGER REFERENCES aoi(id) ON DELETE CASCADE,
  scene_id TEXT NOT NULL,
  observed_at TIMESTAMP,
  cloud_cover NUMERIC,
  ndvi_mean NUMERIC,
  bsi_mean NUMERIC,
  pixel_count INTEGER,
  created_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_satellite_observations_aoi_date
ON satellite_observations (aoi_id, observed_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_satellite_observations_scene_aoi
ON satellite_observations (aoi_id, scene_id);
