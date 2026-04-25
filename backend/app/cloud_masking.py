"""
Advanced cloud masking for Sentinel-2 using SCL band.
Adapted from colleague's visualize.py with our distributed processing.
"""
import numpy as np
from typing import Optional, Tuple

# SCL classes that represent invalid/cloudy pixels
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

def apply_scl_mask(bands_data: dict, scl_data: np.ndarray) -> Tuple[dict, float]:
    """
    Apply sophisticated SCL-based cloud masking to spectral bands.
    
    Args:
        bands_data: Dict with 'blue', 'green', 'red', 'nir', 'swir' numpy arrays
        scl_data: SCL classification numpy array
    
    Returns:
        masked_bands: Dict with same structure, NaN where cloudy
        valid_pixel_ratio: Float 0-1, fraction of pixels kept after masking
    """
    # Build boolean mask: True = bad pixel
    bad_mask = np.zeros(scl_data.shape, dtype=bool)
    for invalid_class in SCL_INVALID:
        bad_mask |= (scl_data == invalid_class)
    
    # Apply mask to all bands
    masked_bands = {}
    total_pixels = scl_data.size
    valid_pixels = np.sum(~bad_mask)
    valid_pixel_ratio = valid_pixels / total_pixels if total_pixels > 0 else 0
    
    for band_name, band_array in bands_data.items():
        masked_array = band_array.copy().astype(np.float32)
        masked_array[bad_mask] = np.nan
        masked_bands[band_name] = masked_array
    
    return masked_bands, valid_pixel_ratio

def compute_indices_masked(masked_bands: dict) -> dict:
    """
    Compute spectral indices on cloud-masked data.
    Handles NaN values gracefully.
    """
    blue = masked_bands['blue'] / S2_SCALE
    green = masked_bands['green'] / S2_SCALE  
    red = masked_bands['red'] / S2_SCALE
    nir = masked_bands['nir'] / S2_SCALE
    swir = masked_bands['swir'] / S2_SCALE
    
    # NDVI with NaN handling
    ndvi = (nir - red) / (nir + red + 1e-6)
    ndvi = np.where(np.isfinite(nir) & np.isfinite(red), ndvi, np.nan)
    
    # BSI with NaN handling  
    bsi = ((swir + red) - (nir + blue)) / ((swir + red) + (nir + blue) + 1e-6)
    bsi = np.where(
        np.isfinite(swir) & np.isfinite(red) & np.isfinite(nir) & np.isfinite(blue),
        bsi, np.nan
    )
    
    # NDWI with NaN handling
    ndwi = (green - nir) / (green + nir + 1e-6)
    ndwi = np.where(np.isfinite(green) & np.isfinite(nir), ndwi, np.nan)
    
    # Plastic Index (from colleague's code) - experimental
    # PI = (nir - green) / (nir + green + swir + 1e-6)
    pi = (nir - green) / (nir + green + swir + 1e-6)
    pi = np.where(
        np.isfinite(nir) & np.isfinite(green) & np.isfinite(swir),
        pi, np.nan
    )
    
    return {
        'ndvi': ndvi,
        'bsi': bsi, 
        'ndwi': ndwi,
        'plastic_index': pi
    }

def aggregate_with_nan_handling(index_array: np.ndarray) -> Tuple[float, int]:
    """
    Aggregate index values handling NaN (masked pixels).
    
    Returns:
        mean_value: Mean of valid (non-NaN) pixels
        valid_count: Number of valid pixels used in calculation
    """
    valid_mask = np.isfinite(index_array)
    valid_pixels = index_array[valid_mask]
    
    if len(valid_pixels) == 0:
        return np.nan, 0
    
    return float(np.mean(valid_pixels)), len(valid_pixels)
