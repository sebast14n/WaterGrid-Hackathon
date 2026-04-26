# Land Change Detection with Google Earth Engine

Detect and visualise yearly vegetation / land-use changes using Landsat 8 imagery and Google Earth Engine.

---

## How it works

For each year the script:

1. Builds a **cloud-free median NDVI composite** for the *start of season* (Jan – Mar by default).
2. Builds a **cloud-free median NDVI composite** for the *end of season* (Jul – Sep by default).
3. Subtracts the two composites → **NDVI difference image**.
4. Exports a colour-coded PNG to the `outputs/` folder:
   - 🟢 **Green** = vegetation *gain* (regrowth, crops)
   - 🔴 **Red** = vegetation *loss* (deforestation, harvest, drought)
   - ⬜ **White** = no significant change

---

## Prerequisites

### 1 — Google Earth Engine account

Sign up at <https://earthengine.google.com> if you don't have one.

### 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### 3 — Authenticate

Run once:

```bash
earthengine authenticate
```

---

## Configuration (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `AOI` | Central Romania | Bounding rectangle `[W, S, E, N]` |
| `YEARS` | `[2020, 2021, 2022]` | Years to analyse |
| `SEASON_START_MONTHS` | Jan – Mar | Baseline (winter/early spring) |
| `SEASON_END_MONTHS` | Jul – Sep | Peak growing season |
| `SCALE` | `30` | Output resolution in metres |
| `OUTPUT_DIR` | `outputs/` | Local folder for PNGs |

---

## Run

### Option A — Batch export (PNGs)
```bash
python land_change.py
```
Output PNGs will be written to `outputs/land_change_<year>.png`.

### Option B — Interactive notebook (geemap)
Open `explore.ipynb` in VS Code or JupyterLab.  
Each year is a toggleable layer on a live satellite basemap.

### Option C — Streamlit web app
```bash
streamlit run app.py
```
Opens a browser at `http://localhost:8501`.  
Select a year, adjust season windows in the sidebar, and click **▶ Compute & Display**.  
The right panel shows any PNGs already exported by Option A.
