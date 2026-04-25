"""
Land change detection between consecutive yearly Sentinel-2 composites.
Uses geoai ChangeStarDetection (zero-shot building / land change model).

Usage:
    python3 land_change.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from geoai.change_detection import ChangeStarDetection, list_changestar_models

from config import YEARS, TIFF_DIR, CHANGE_DIR


def run_change_detection(
    year_before: int,
    year_after: int,
    overwrite: bool = False,
) -> dict | None:
    """Run ChangeStarDetection between two yearly GeoTIFFs.

    Outputs:
      - change_<before>_<after>.tif   — binary change mask
      - change_<before>_<after>.gpkg  — change polygons (vector)
    """
    before_tif = TIFF_DIR / f"sentinel2_{year_before}.tif"
    after_tif  = TIFF_DIR / f"sentinel2_{year_after}.tif"

    out_tif  = CHANGE_DIR / f"change_{year_before}_{year_after}.tif"
    out_vec  = CHANGE_DIR / f"change_{year_before}_{year_after}.gpkg"

    if not before_tif.exists():
        print(f"Missing: {before_tif}  — run download_images.py first.", file=sys.stderr)
        return None
    if not after_tif.exists():
        print(f"Missing: {after_tif}  — run download_images.py first.", file=sys.stderr)
        return None

    if out_tif.exists() and not overwrite:
        print(f"Already exists: {out_tif}")
        return {"change_map": None, "output": str(out_tif)}

    print(f"Detecting changes {year_before} → {year_after} ...")
    detector = ChangeStarDetection(model_name="s1_s1c1_vitb")
    result = detector.predict(
        image1_path=str(before_tif),
        image2_path=str(after_tif),
        output_change=str(out_tif),
        output_vector=str(out_vec),
        tile_size=1024,
        overlap=64,
        threshold=0.5,
    )

    print(f"Saved change mask  : {out_tif}")
    if out_vec.exists():
        print(f"Saved change vector: {out_vec}")
    return result


def main():
    # Process each consecutive pair of years
    pairs = list(zip(YEARS[:-1], YEARS[1:]))
    if not pairs:
        print("Need at least 2 years in config.YEARS.")
        return
    for before, after in pairs:
        run_change_detection(before, after)
    print("Change detection complete.")


if __name__ == "__main__":
    main()
