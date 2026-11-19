"""
aggregate_weather_risk.py
=========================
Aggregate raster-pipeline risk COGs to county-level scores and write
public/data/weather_risk_by_county.json for the frontend layer.

Data source
-----------
Reads output/raster/v1/latest.json to locate the current batch, then reads
the first 24 hourly risk COGs (hours 0–23).  For each pixel the max risk score
across those 24 hours is computed, then per-county mean of that max is derived
using Census county boundaries (fetched from the TIGER CDN or a local cache).

Output schema
-------------
{
  "counties":       { "<fips>": <float 0–1>, ... },
  "batch_id":       "<hex>",
  "cutoff":         "<ISO-8601 UTC>",
  "fixture":        <bool>,
  "calibrated":     false,
  "heuristic_score": true
}

Input requirements
------------------
Reads the most recently published complete batch. Live mode refuses synthetic
fixtures and never substitutes them for real weather. Use --allow-fixture only
for an explicitly labeled local demonstration.

Run from power_outage_model/:
  .venv312/bin/python scripts/aggregate_weather_risk.py
"""

from __future__ import annotations

import io
import argparse
import json
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import rasterio.mask
import shapefile  # pyshp
from shapely.geometry import shape, mapping, box
from shapely.ops import transform as shp_transform
from pyproj import Transformer

# ---------------------------------------------------------------------------
# Paths (all relative to power_outage_model/)
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).parent
REPO_ROOT    = SCRIPT_DIR.parent.parent           # see_your_power/
PM_ROOT      = SCRIPT_DIR.parent                  # power_outage_model/
LATEST_JSON  = PM_ROOT / "output/raster/v1/latest.json"
OUTPUT_JSON  = REPO_ROOT / "public/data/weather_risk_by_county.json"

# Census county boundary ZIP (20 m generalised)
COUNTY_ZIP_URL   = "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_county_20m.zip"
COUNTY_ZIP_CACHE = PM_ROOT / ".cache/static-inputs/cb_2025_us_county_20m.zip"

NODATA    = -9999.0
MAX_HOURS = 24   # first 24 hourly intervals (hours 0–23)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_risk_tif_paths(batch_dir: Path) -> list[str]:
    """Return sorted list of risk COG absolute paths for the batch."""
    risk_dir = batch_dir / "risk"
    return sorted(str(t) for t in risk_dir.glob("*.tif"))


def compute_24h_max(
    tif_paths: list[str],
) -> tuple[np.ndarray, Any, Any]:
    """Stack first MAX_HOURS risk COGs and compute per-pixel max.

    Returns (max_array, transform, crs) where max_array contains NaN for NoData.
    """
    selected = tif_paths[:MAX_HOURS]
    stack: list[np.ndarray] = []
    transform = None
    crs = None
    for path in selected:
        with rasterio.open(path) as src:
            data = src.read(1).astype(np.float32)
            data[data == NODATA] = np.nan
            stack.append(data)
            if transform is None:
                transform = src.transform
                crs = src.crs
    arr = np.stack(stack, axis=0)
    max_arr = np.nanmax(arr, axis=0)
    return max_arr, transform, crs


def fetch_county_zip() -> Path:
    """Return path to county boundary ZIP, downloading if needed."""
    if COUNTY_ZIP_CACHE.exists():
        print(f"  Using cached county boundaries: {COUNTY_ZIP_CACHE}", flush=True)
        return COUNTY_ZIP_CACHE
    print(f"  Downloading county boundaries from {COUNTY_ZIP_URL} …", flush=True)
    COUNTY_ZIP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(COUNTY_ZIP_URL, COUNTY_ZIP_CACHE)
    print(f"  Saved to {COUNTY_ZIP_CACHE}", flush=True)
    return COUNTY_ZIP_CACHE


def _open_shapefile_from_zip(zip_path: Path) -> shapefile.Reader:
    """Open the .shp inside a ZIP using pyshp with in-memory buffers."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        shp_name = next(n for n in names if n.endswith(".shp"))
        base = shp_name[:-4]
        shp_bytes = zf.read(shp_name)
        dbf_bytes = zf.read(base + ".dbf")
        shx_name = base + ".shx"
        shx_bytes = zf.read(shx_name) if shx_name in names else None

    kwargs: dict[str, Any] = {
        "shp": io.BytesIO(shp_bytes),
        "dbf": io.BytesIO(dbf_bytes),
    }
    if shx_bytes is not None:
        kwargs["shx"] = io.BytesIO(shx_bytes)
    return shapefile.Reader(**kwargs)


def aggregate_to_counties(
    max_arr: np.ndarray,
    transform,
    crs,
    county_zip: Path,
) -> dict[str, float]:
    """Compute mean of 24h-max risk score per county polygon.

    Uses rasterio.mask.  Only counties whose bbox overlaps the raster extent
    get a value.
    """
    height, width = max_arr.shape
    raster_bounds = rasterio.transform.array_bounds(height, width, transform)
    raster_box = box(*raster_bounds)

    # Transformer: county source (EPSG:4326) → raster CRS
    epsg = crs.to_epsg()
    if epsg and epsg != 4326:
        transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        need_reproject = True
    else:
        transformer = None
        need_reproject = False

    results: dict[str, float] = {}

    # Write max_arr to an in-memory rasterio dataset for masking
    with rasterio.MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="float32",
            crs=crs,
            transform=transform,
            nodata=np.nan,
        ) as dst:
            dst.write(max_arr, 1)

        with memfile.open() as src_raster:
            sf = _open_shapefile_from_zip(county_zip)
            # Build field name → index map
            field_names = [f[0] for f in sf.fields[1:]]  # skip DeletionFlag

            geoid_idx = None
            for candidate in ("GEOID", "GEOID10", "GEO_ID"):
                if candidate in field_names:
                    geoid_idx = field_names.index(candidate)
                    break

            for rec, geom_rec in zip(sf.records(), sf.shapes()):
                fips = rec[geoid_idx] if geoid_idx is not None else ""
                geom = shape(geom_rec.__geo_interface__)

                # Quick bbox filter
                if not raster_box.intersects(geom):
                    continue

                if need_reproject and transformer:
                    geom = shp_transform(transformer.transform, geom)

                try:
                    out_image, _ = rasterio.mask.mask(
                        src_raster,
                        [mapping(geom)],
                        crop=True,
                        nodata=np.nan,
                    )
                    vals = out_image[0]
                    valid = vals[~np.isnan(vals)]
                    if valid.size == 0:
                        continue
                    results[fips] = float(np.mean(valid))
                except Exception as exc:
                    print(f"    WARNING: mask failed for {fips}: {exc}", flush=True)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-fixture", action="store_true")
    parser.add_argument("--raster-output", type=Path, default=PM_ROOT / "output")
    parser.add_argument("--output", type=Path, default=OUTPUT_JSON)
    args = parser.parse_args()
    print("aggregate_weather_risk.py — published raster batch", flush=True)

    # Load latest.json
    with open(args.raster_output / "raster/v1/latest.json") as f:
        latest = json.load(f)

    batch_id  = latest["batch_id"]
    cutoff    = latest["cutoff"]
    batch_dir = args.raster_output / "raster/v1/runs" / batch_id
    print(f"  Batch: {batch_id}  cutoff: {cutoff}", flush=True)

    # Read manifest to check fixture flag
    manifest_path = batch_dir / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)
    is_fixture = manifest.get("fixture", False)
    if is_fixture and not args.allow_fixture:
        raise ValueError("Refusing fixture weather in live mode; publish a real batch first")
    if manifest.get("quality_status") != "complete" or len(manifest.get("intervals", [])) != 72:
        raise ValueError("A complete 72-hour published batch is required")

    # Collect risk COG paths
    tif_paths = load_risk_tif_paths(batch_dir)
    print(f"  Found {len(tif_paths)} risk COGs; using first {MAX_HOURS}", flush=True)

    # Compute 24h pixel-level max
    print("  Computing 24h pixel-level max risk …", flush=True)
    max_arr, transform, crs = compute_24h_max(tif_paths)
    print(f"  Max array shape: {max_arr.shape}  value range: "
          f"[{np.nanmin(max_arr):.4f}, {np.nanmax(max_arr):.4f}]", flush=True)

    # Fetch / use cached county boundaries
    county_zip = fetch_county_zip()

    # Aggregate to counties
    print("  Aggregating to county polygons …", flush=True)
    county_scores = aggregate_to_counties(max_arr, transform, crs, county_zip)
    print(f"  Counties with values: {len(county_scores)}", flush=True)
    for fips, score in sorted(county_scores.items()):
        print(f"    {fips}: {score:.6f}", flush=True)

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "counties":        county_scores,
        "batch_id":        batch_id,
        "cutoff":          cutoff,
        "fixture":         is_fixture,
        "calibrated":      False,
        "heuristic_score": True,
        "coverage_policy": manifest.get("coverage_policy", "complete_land"),
        "coverage_summary": manifest.get("coverage_summary"),
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote {args.output}", flush=True)
    print(f"County count: {len(county_scores)}", flush=True)
    if is_fixture:
        print("NOTE: fixture=true — only 1–2 counties covered (2-cell synthetic grid)", flush=True)


if __name__ == "__main__":
    main()
