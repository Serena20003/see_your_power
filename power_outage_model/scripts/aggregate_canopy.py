"""
aggregate_canopy.py — Pre-aggregate NLCD 2025 TCC raster to county-level mean canopy cover.

Source raster : .cache/static-inputs/nlcd-tcc-2025-v2025-6/
                nlcd_tcc_conus_wgs84_v2025-6_20250101_20251231.tif
  CRS         : Albers Equal Area (CONUS)   (NOT WGS84 despite the filename)
  dtype       : uint8  0–100 = canopy cover %,  255 = nodata
  Size        : 154 180 × 97 279 px  (~3.6 GB) — do NOT load into memory

County boundaries : Census cb_2025_us_county_20m.zip (downloaded on first run,
                    cached as .cache/static-inputs/cb_2025_us_county_20m.zip)
  GEOID field : 5-digit FIPS string (STATE || COUNTY)

Output : public/data/canopy_by_county.json
  { "counties": { "<fips>": <mean_pct float>, ... }, "vintage": "NLCD-2025-v2025-6" }

Strategy: open the raster once; for each county polygon reproject the geometry
to Albers, read a small windowed tile (bounded by the reprojected bbox), apply
rasterio.mask to get valid pixels (uint8 ≤ 100), compute mean, and write result.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import rasterio.mask
import rasterio.transform
import shapefile  # pyshp — no fiona required
from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform as shp_transform

# ── Paths ───────────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).resolve().parents[2]
RASTER_PATH = REPO_ROOT / "power_outage_model" / ".cache" / "static-inputs" / \
              "nlcd-tcc-2025-v2025-6" / \
              "nlcd_tcc_conus_wgs84_v2025-6_20250101_20251231.tif"
COUNTY_ZIP  = REPO_ROOT / "power_outage_model" / ".cache" / "static-inputs" / \
              "cb_2025_us_county_20m.zip"
OUT_PATH    = REPO_ROOT / "public" / "data" / "canopy_by_county.json"

COUNTY_ZIP_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_county_20m.zip"
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def ensure_county_zip() -> None:
    """Download the county boundary ZIP if not already cached."""
    if COUNTY_ZIP.exists():
        return
    print(f"Downloading {COUNTY_ZIP_URL} …", flush=True)
    urllib.request.urlretrieve(COUNTY_ZIP_URL, COUNTY_ZIP)
    print(f"Saved → {COUNTY_ZIP}", flush=True)


def load_county_polygons() -> list[tuple[str, object]]:
    """
    Read the Census county shapefile from the ZIP using pyshp.
    Returns a list of (fips_5digit, shapely_geometry_EPSG4326) tuples.
    """
    ensure_county_zip()
    with zipfile.ZipFile(COUNTY_ZIP) as zf:
        names = zf.namelist()
        shp_name = next(n for n in names if n.endswith(".shp"))
        base = shp_name[:-4]
        shp_bytes = zf.read(base + ".shp")
        dbf_bytes = zf.read(base + ".dbf")
        shx_bytes = zf.read(base + ".shx") if base + ".shx" in names else None

    kwargs: dict = dict(
        shp=io.BytesIO(shp_bytes),
        dbf=io.BytesIO(dbf_bytes),
    )
    if shx_bytes is not None:
        kwargs["shx"] = io.BytesIO(shx_bytes)

    sf  = shapefile.Reader(**kwargs)
    fields = [f[0] for f in sf.fields[1:]]  # skip DeletionFlag
    geoid_idx = fields.index("GEOID")

    counties = []
    for rec in sf.iterShapeRecords():
        geoid = str(rec.record[geoid_idx])
        geom  = shape(rec.shape.__geo_interface__)
        counties.append((geoid, geom))
    return counties


def reproject_polygon(geom, transformer: Transformer):
    """Reproject a Shapely geometry using a pyproj Transformer (always_xy)."""
    def _transform(x, y, z=None):
        xx, yy = transformer.transform(x, y)
        return (xx, yy) if z is None else (xx, yy, z)
    return shp_transform(_transform, geom)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Raster : {RASTER_PATH}", flush=True)
    print(f"Output : {OUT_PATH}", flush=True)

    with rasterio.open(RASTER_PATH) as src:
        raster_crs = src.crs

        # Build a transformer from EPSG:4326 (county polygons) → raster CRS.
        transformer = Transformer.from_crs("EPSG:4326", raster_crs, always_xy=True)

        print("Loading county polygons …", flush=True)
        counties = load_county_polygons()
        print(f"  {len(counties)} counties loaded.", flush=True)

        results: dict[str, float | None] = {}
        n_total  = len(counties)
        n_done   = 0
        n_nodata = 0

        for fips, geom_4326 in counties:
            # Reproject polygon to raster CRS (Albers).
            geom_albers = reproject_polygon(geom_4326, transformer)

            # rasterio.mask: crop raster to polygon, return only valid pixels.
            try:
                masked, _ = rasterio.mask.mask(
                    src,
                    [mapping(geom_albers)],
                    crop=True,
                    nodata=255,
                    all_touched=False,
                    filled=True,
                )
            except Exception:
                # County outside raster bounds (e.g. Puerto Rico, Alaska).
                results[fips] = None
                n_nodata += 1
                n_done += 1
                continue

            band = masked[0]  # shape (rows, cols), dtype uint8
            valid = band[band <= 100]  # nodata=255, also exclude 101–254 defensively

            if valid.size == 0:
                results[fips] = None
                n_nodata += 1
            else:
                results[fips] = float(np.mean(valid))

            n_done += 1
            if n_done % 100 == 0:
                pct = 100 * n_done / n_total
                print(f"  {n_done}/{n_total} ({pct:.0f}%) …", flush=True)

    # Remove None entries before writing (counties outside CONUS raster extent).
    counties_out = {k: round(v, 2) for k, v in results.items() if v is not None}

    print(f"\n{len(counties_out)} counties with valid canopy values "
          f"({n_nodata} outside raster/no data).", flush=True)

    # Spot-check
    king     = counties_out.get("53033")
    maricopa = counties_out.get("04013")
    print(f"King County WA  (53033): {king}%  (expect high — forested)")
    print(f"Maricopa AZ     (04013): {maricopa}%  (expect low — desert)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "counties": counties_out,
        "vintage":  "NLCD-2025-v2025-6",
    }
    with open(OUT_PATH, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    print(f"\nWrote {OUT_PATH} ({OUT_PATH.stat().st_size // 1024} KB)", flush=True)


if __name__ == "__main__":
    main()
