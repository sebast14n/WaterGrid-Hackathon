"""
Visualization helpers for monthly Sentinel-2 GeoTIFFs.

Processing flow (adapted from the MODIS cloud-mask concept):
  load GeoTIFF → clip to AOI → SCL-based pixel cloud mask
  → spectral indices (NDVI / NDWI / MNDWI / BSI)
  → RGB composite / colourmap renders / timelapse / diff map

Cloud masking concept (inspired by Soumyabrata/MODIS-cloud-mask):
  MODIS uses a dedicated quality bitmask band to flag bad pixels before analysis.
  Sentinel-2 L2A provides the same via the SCL (Scene Classification Layer) band.
  We apply this mask BEFORE any visualization or index calculation.
"""
from __future__ import annotations

import calendar
import io
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import rioxarray as rxr

from config import AOI_BBOX, TIFF_DIR

# Band indices in the merged GeoTIFF (order: B02, B03, B04, B08, B8A, B11, B12, SCL)
B_BLUE  = 0   # B02
B_GREEN = 1   # B03
B_RED   = 2   # B04
B_NIR   = 3   # B08
B_SWIR1 = 5   # B11
B_SWIR2 = 6   # B12
B_SCL   = 7   # Scene Classification Layer

# SCL classes that represent invalid/cloudy pixels — set to NaN before analysis
# Reference: Sentinel-2 Level-2A Algorithm Theoretical Basis Document (ESA)
SCL_INVALID = frozenset({
    0,   # No data
    1,   # Saturated / defective
    3,   # Cloud shadow
    8,   # Cloud — medium probability
    9,   # Cloud — high probability
    10,  # Thin cirrus
    11,  # Snow / ice
})

# Reflectance scale factor (Sentinel-2 L2A stores values as 0–10000)
S2_SCALE = 10_000.0


# ── Cloud masking ─────────────────────────────────────────────────────────────

def _apply_scl_mask(arr: np.ndarray) -> np.ndarray:
    """Apply pixel-level cloud/shadow mask using the SCL band.

    Concept from MODIS-cloud-mask (github.com/Soumyabrata/MODIS-cloud-mask):
    use a quality classification band to identify bad pixels and zero/NaN them
    out before any spectral analysis — identical workflow, different sensor.

    Parameters
    ----------
    arr : float32 (bands, H, W) — already scaled to [0, 1]; SCL band still raw

    Returns
    -------
    arr : same shape, with cloudy/invalid pixels set to NaN in all spectral bands
    """
    scl = arr[B_SCL]  # raw SCL integer values (stored as float after astype)

    # Build boolean mask: True = bad pixel (cloud / shadow / snow / no-data)
    bad = np.zeros(scl.shape, dtype=bool)
    for cls in SCL_INVALID:
        bad |= (scl == cls)

    # Set all spectral bands to NaN where the pixel is flagged
    out = arr.copy()
    out[:, bad] = np.nan
    return out


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _read_aoi(tif_path: Path) -> np.ndarray:
    """Load a Sentinel-2 GeoTIFF, clip to AOI, scale to reflectance, and cloud-mask.

    Processing order (mirrors the MODIS-cloud-mask pipeline):
      1. Read raw DN values
      2. Clip to AOI bounding box
      3. Scale spectral bands to [0, 1]
      4. Apply SCL-based cloud/shadow mask  ← must happen before any analysis

    Returns
    -------
    arr : float32 (bands, H, W), reflectance in [0, 1], NaN where cloudy/invalid
    """
    ds = rxr.open_rasterio(tif_path, masked=True)

    # 1. Clip to AOI bounding box
    ds = ds.rio.clip_box(
        minx=AOI_BBOX[0], miny=AOI_BBOX[1],
        maxx=AOI_BBOX[2], maxy=AOI_BBOX[3],
        crs="EPSG:4326",
    )
    raw = ds.values.astype(np.float32)  # (bands, H, W)

    # 2. Scale to reflectance (SCL band stays as raw class integers for masking)
    scaled = raw / S2_SCALE

    # 3. Apply pixel-level cloud mask using the SCL band — BEFORE any analysis
    return _apply_scl_mask(scaled)


def _to_rgb(arr: np.ndarray, percentile_low=2, percentile_high=98) -> np.ndarray:
    """Contrast-stretch RGB bands from a cloud-masked float32 array.
    Returns uint8 (H, W, 3).
    """
    rgb = arr[[B_RED, B_GREEN, B_BLUE]]  # (3, H, W)
    out = np.zeros((rgb.shape[1], rgb.shape[2], 3), dtype=np.uint8)
    for i in range(3):
        ch = rgb[i]
        valid = ch[~np.isnan(ch)]
        if valid.size == 0:
            continue
        lo = np.percentile(valid, percentile_low)
        hi = np.percentile(valid, percentile_high)
        if hi == lo:
            hi = lo + 1e-6
        stretched = np.clip((ch - lo) / (hi - lo), 0, 1)
        stretched = np.where(np.isnan(stretched), 0, stretched)
        out[:, :, i] = (stretched * 255).astype(np.uint8)
    return out


# ── Spectral indices (all require cloud-masked float32 input) ─────────────────

def compute_ndvi(arr: np.ndarray) -> np.ndarray:
    """NDVI = (NIR - Red) / (NIR + Red). Range -1..1."""
    nir, red = arr[B_NIR], arr[B_RED]
    with np.errstate(invalid="ignore", divide="ignore"):
        idx = (nir - red) / (nir + red)
    return np.where(np.isnan(nir) | np.isnan(red), np.nan, idx)


def compute_ndwi(arr: np.ndarray) -> np.ndarray:
    """NDWI = (Green - NIR) / (Green + NIR). Highlights open water."""
    green, nir = arr[B_GREEN], arr[B_NIR]
    with np.errstate(invalid="ignore", divide="ignore"):
        idx = (green - nir) / (green + nir)
    return np.where(np.isnan(green) | np.isnan(nir), np.nan, idx)


def compute_mndwi(arr: np.ndarray) -> np.ndarray:
    """MNDWI = (Green - SWIR1) / (Green + SWIR1). Better for turbid/urban water."""
    green, swir1 = arr[B_GREEN], arr[B_SWIR1]
    with np.errstate(invalid="ignore", divide="ignore"):
        idx = (green - swir1) / (green + swir1)
    return np.where(np.isnan(green) | np.isnan(swir1), np.nan, idx)


def compute_bsi(arr: np.ndarray) -> np.ndarray:
    """BSI = ((SWIR1+Red) - (NIR+Blue)) / ((SWIR1+Red) + (NIR+Blue)).
    Highlights bare soil / disturbed land. Positive = bare/disturbed.
    """
    blue, red = arr[B_BLUE], arr[B_RED]
    nir, swir1 = arr[B_NIR], arr[B_SWIR1]
    num = (swir1 + red) - (nir + blue)
    den = (swir1 + red) + (nir + blue)
    with np.errstate(invalid="ignore", divide="ignore"):
        idx = num / den
    bad = np.isnan(blue) | np.isnan(red) | np.isnan(nir) | np.isnan(swir1)
    return np.where(bad, np.nan, idx)


# ── Public rendering API ──────────────────────────────────────────────────────

def tif_to_rgb_png(tif_path: Path) -> bytes:
    """RGB PNG from a Sentinel-2 GeoTIFF. Returns PNG bytes."""
    arr = _read_aoi(tif_path)
    rgb = _to_rgb(arr)
    fig, ax = plt.subplots(figsize=(4, 4), dpi=100)
    ax.imshow(rgb)
    ax.axis("off")
    fig.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def tif_to_ndvi_png(tif_path: Path) -> bytes:
    """NDVI colourmap PNG. Returns PNG bytes."""
    arr = _read_aoi(tif_path)
    ndvi = compute_ndvi(arr)
    fig, ax = plt.subplots(figsize=(4, 4), dpi=100)
    im = ax.imshow(ndvi, cmap="RdYlGn", vmin=-0.2, vmax=0.8)
    ax.axis("off")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="NDVI")
    fig.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def tif_to_indices_png(tif_path: Path, label: str = "") -> bytes:
    """4-panel image: RGB | NDVI | MNDWI | BSI. Returns PNG bytes."""
    arr   = _read_aoi(tif_path)
    rgb   = _to_rgb(arr)
    ndvi  = compute_ndvi(arr)
    mndwi = compute_mndwi(arr)
    bsi   = compute_bsi(arr)

    fig, axes = plt.subplots(1, 4, figsize=(16, 4), dpi=100)
    titles = ["RGB", "NDVI", "MNDWI (water)", "BSI (bare soil)"]
    cmaps  = [None, "RdYlGn", "RdYlBu", "YlOrBr"]
    data   = [rgb, ndvi, mndwi, bsi]
    vlims  = [None, (-0.2, 0.8), (-0.5, 0.5), (-0.5, 0.5)]

    for ax, d, t, cm, vl in zip(axes, data, titles, cmaps, vlims):
        if cm is None:
            ax.imshow(d)
        else:
            kw = {"cmap": cm, "vmin": vl[0], "vmax": vl[1]}
            im = ax.imshow(d, **kw)
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        ax.set_title(t, fontsize=9)
        ax.axis("off")

    fig.suptitle(label, fontsize=10)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def make_timelapse_gif(tif_paths: list[Path], fps: float = 1.5, labels: list[str] | None = None) -> bytes:
    """Animated GIF of RGB frames. Returns GIF bytes."""
    import imageio.v3 as iio

    frames = []
    for i, p in enumerate(tif_paths):
        arr   = _read_aoi(p)
        rgb   = _to_rgb(arr)
        label = labels[i] if labels else p.stem

        fig, ax = plt.subplots(figsize=(4, 4), dpi=100)
        ax.imshow(rgb)
        ax.axis("off")
        ax.set_title(label, fontsize=10, color="white",
                     bbox=dict(facecolor="black", alpha=0.6, pad=3))
        fig.tight_layout(pad=0)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0)
        plt.close(fig)
        buf.seek(0)
        frames.append(iio.imread(buf, extension=".png"))

    if not frames:
        raise ValueError("No frames to create GIF.")

    max_h = max(f.shape[0] for f in frames)
    max_w = max(f.shape[1] for f in frames)
    padded = []
    for f in frames:
        ph, pw = max_h - f.shape[0], max_w - f.shape[1]
        if ph or pw:
            f = np.pad(f, ((0, ph), (0, pw), (0, 0)))
        padded.append(f)

    buf = io.BytesIO()
    iio.imwrite(buf, padded, extension=".gif", loop=0, duration=int(1000 / fps))
    buf.seek(0)
    return buf.read()


def ndvi_mean_per_tif(tif_path: Path) -> float:
    """Mean NDVI over the AOI."""
    arr  = _read_aoi(tif_path)
    ndvi   = compute_ndvi(arr)
    valid  = ndvi[~np.isnan(ndvi)]
    return float(np.mean(valid)) if valid.size > 0 else float("nan")


def ndvi_timeseries_png(tif_paths: list[Path], labels: list[str]) -> bytes:
    """NDVI mean-over-time chart. Returns PNG bytes."""
    values = [ndvi_mean_per_tif(p) for p in tif_paths]

    fig, ax = plt.subplots(figsize=(10, 3.5), dpi=110)
    x = list(range(len(labels)))
    safe = [v if not np.isnan(v) else 0 for v in values]

    ax.fill_between(x, safe, alpha=0.25, color="green")
    ax.plot(x, [v if not np.isnan(v) else np.nan for v in values],
            "o-", color="darkgreen", linewidth=2, markersize=7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Mean NDVI")
    ax.set_title("NDVI Time Series — Balastiera AOI")
    ax.set_ylim(-0.1, 0.9)
    ax.axhline(0.3, color="gray", linestyle="--", alpha=0.5, label="Healthy veg. threshold")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def diff_map_png(tif_before: Path, tif_after: Path,
                 label_before: str = "Before", label_after: str = "After") -> bytes:
    """4-panel change map: RGB before | RGB after | NDVI Δ | BSI Δ.
    Returns PNG bytes.
    """
    arr_b = _read_aoi(tif_before)
    arr_a = _read_aoi(tif_after)

    rgb_b   = _to_rgb(arr_b)
    rgb_a   = _to_rgb(arr_a)
    d_ndvi  = compute_ndvi(arr_a)  - compute_ndvi(arr_b)
    d_bsi   = compute_bsi(arr_a)   - compute_bsi(arr_b)

    fig, axes = plt.subplots(1, 4, figsize=(17, 4.5), dpi=110)

    axes[0].imshow(rgb_b)
    axes[0].set_title(f"Before — {label_before}", fontsize=9)
    axes[0].axis("off")

    axes[1].imshow(rgb_a)
    axes[1].set_title(f"After — {label_after}", fontsize=9)
    axes[1].axis("off")

    vmax = 0.4
    im2 = axes[2].imshow(d_ndvi, cmap="RdYlGn", vmin=-vmax, vmax=vmax)
    axes[2].set_title("ΔNDVI\ngreen=veg gain  red=loss", fontsize=9)
    axes[2].axis("off")
    fig.colorbar(im2, ax=axes[2], fraction=0.046, pad=0.04)

    im3 = axes[3].imshow(d_bsi, cmap="RdYlBu_r", vmin=-vmax, vmax=vmax)
    axes[3].set_title("ΔBSI\norange=bare soil increase", fontsize=9)
    axes[3].axis("off")
    fig.colorbar(im3, ax=axes[3], fraction=0.046, pad=0.04)

    fig.suptitle(f"Land change: {label_before} → {label_after}", fontsize=12)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
