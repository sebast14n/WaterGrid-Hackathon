"""
Download Sentinel-2 imagery for each year (or month) from Microsoft Planetary Computer.
Produces a merged multi-band GeoTIFF per year/month in outputs/tiffs/.

Usage:
    python3 download_images.py
"""
from __future__ import annotations

import calendar
import sys
from pathlib import Path

import geoai
from geoai.download import pc_stac_search, download_pc_stac_item

from config import (
    AOI_BBOX,
    YEARS,
    S2_COLLECTION,
    S2_CLOUD_MAX,
    S2_ALL_BANDS,
    TIFF_DIR,
    time_range_for_year,
)

MONTH_NAMES = [calendar.month_abbr[m] for m in range(1, 13)]  # Jan..Dec


def download_month(year: int, month: int, overwrite: bool = False) -> Path | None:
    """Download the least-cloudy Sentinel-2 scene for a single calendar month.

    Output: outputs/tiffs/sentinel2_<year>_<MM>.tif
    Returns the path, or None on failure.
    """
    mm = f"{month:02d}"
    out_path = TIFF_DIR / f"sentinel2_{year}_{mm}.tif"

    if out_path.exists() and not overwrite:
        print(f"[{year}-{mm}] Already exists: {out_path}")
        return out_path

    last_day = calendar.monthrange(year, month)[1]
    time_range = f"{year}-{mm}-01/{year}-{mm}-{last_day}"
    print(f"[{year}-{mm}] Searching {S2_COLLECTION} {time_range} ...")

    # Try progressively relaxed cloud thresholds
    for cloud_limit in (S2_CLOUD_MAX, 50, 80):
        items = pc_stac_search(
            collection=S2_COLLECTION,
            bbox=AOI_BBOX,
            time_range=time_range,
            query={"eo:cloud_cover": {"lt": cloud_limit}},
            max_items=20,
            quiet=True,
        )
        if items:
            break

    if not items:
        print(f"[{year}-{mm}] WARNING: No scenes found — skipping.", file=sys.stderr)
        return None

    best = min(items, key=lambda i: i.properties.get("eo:cloud_cover", 100))
    cloud_pct = best.properties.get("eo:cloud_cover", "?")
    print(f"[{year}-{mm}] Best scene: {best.id}  cloud={cloud_pct:.1f}%")

    item_url = best.get_self_href() or (
        f"https://planetarycomputer.microsoft.com/api/stac/v1"
        f"/collections/{S2_COLLECTION}/items/{best.id}"
    )

    band_dir = TIFF_DIR / f"{year}_{mm}_bands"
    band_dir.mkdir(exist_ok=True)

    print(f"[{year}-{mm}] Downloading → {band_dir} ...")
    result = download_pc_stac_item(
        item_url=item_url,
        bands=S2_ALL_BANDS,
        output_dir=str(band_dir),
        show_progress=True,
        merge_bands=True,
        merged_filename=str(out_path),
        overwrite=overwrite,
        cell_size=10,
    )

    if out_path.exists():
        print(f"[{year}-{mm}] Saved: {out_path}")
        return out_path

    merged = result.get("merged") if isinstance(result, dict) else None
    if merged and Path(merged).exists():
        Path(merged).rename(out_path)
        print(f"[{year}-{mm}] Saved: {out_path}")
        return out_path

    print(f"[{year}-{mm}] ERROR: merged GeoTIFF not found.", file=sys.stderr)
    return None


def download_all_months(year: int, overwrite: bool = False) -> list[Path]:
    """Download all 12 monthly scenes for *year*. Returns list of saved paths."""
    paths = []
    for month in range(1, 13):
        p = download_month(year, month, overwrite=overwrite)
        if p:
            paths.append(p)
    return paths


def download_year(year: int, overwrite: bool = False) -> Path | None:
    """Search Planetary Computer for the least-cloudy Sentinel-2 scene in
    *year* over the AOI and download it as a merged GeoTIFF.

    Returns the path to the merged GeoTIFF, or None on failure.
    """
    out_path = TIFF_DIR / f"sentinel2_{year}.tif"
    if out_path.exists() and not overwrite:
        print(f"[{year}] Already exists: {out_path}")
        return out_path

    time_range = time_range_for_year(year)
    print(f"[{year}] Searching {S2_COLLECTION} {time_range} ...")

    items = pc_stac_search(
        collection=S2_COLLECTION,
        bbox=AOI_BBOX,
        time_range=time_range,
        query={"eo:cloud_cover": {"lt": S2_CLOUD_MAX}},
        max_items=20,
        quiet=True,
    )

    if not items:
        # Relax cloud threshold and retry
        print(f"[{year}] No items <{S2_CLOUD_MAX}% cloud. Retrying with <50% ...")
        items = pc_stac_search(
            collection=S2_COLLECTION,
            bbox=AOI_BBOX,
            time_range=time_range,
            query={"eo:cloud_cover": {"lt": 50}},
            max_items=20,
            quiet=True,
        )

    if not items:
        print(f"[{year}] WARNING: No Sentinel-2 scenes found — skipping.", file=sys.stderr)
        return None

    # Pick the scene with the lowest cloud cover
    items_sorted = sorted(items, key=lambda i: i.properties.get("eo:cloud_cover", 100))
    best = items_sorted[0]
    cloud_pct = best.properties.get("eo:cloud_cover", "?")
    print(f"[{year}] Best scene: {best.id}  cloud={cloud_pct}%")

    item_url = best.get_self_href()
    if item_url is None:
        # Construct URL from collection + item id
        item_url = (
            f"https://planetarycomputer.microsoft.com/api/stac/v1"
            f"/collections/{S2_COLLECTION}/items/{best.id}"
        )

    band_dir = TIFF_DIR / f"{year}_bands"
    band_dir.mkdir(exist_ok=True)

    print(f"[{year}] Downloading bands → {band_dir} ...")
    result = download_pc_stac_item(
        item_url=item_url,
        bands=S2_ALL_BANDS,
        output_dir=str(band_dir),
        show_progress=True,
        merge_bands=True,
        merged_filename=str(out_path),
        overwrite=overwrite,
        cell_size=10,
    )

    if out_path.exists():
        print(f"[{year}] Saved: {out_path}")
        return out_path

    # Fallback: merged key may contain the path
    merged = result.get("merged")
    if merged and Path(merged).exists():
        Path(merged).rename(out_path)
        print(f"[{year}] Saved: {out_path}")
        return out_path

    print(f"[{year}] ERROR: merged GeoTIFF not found.", file=sys.stderr)
    return None


def main():
    print(f"Downloading Sentinel-2 for years: {YEARS}")
    for year in YEARS:
        download_year(year)
    print("Done.")


if __name__ == "__main__":
    main()
