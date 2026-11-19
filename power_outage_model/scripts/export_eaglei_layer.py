"""
export_eaglei_layer.py
======================
Reads all predictions JSON files produced by the experimental EAGLE-I
outage model (experimental-random-points/v1) and writes a compact JSON
file for use as a Mapbox choropleth layer in the front-end.

Source data
-----------
  Power outage model: power_outage_model/output/outage/
  Run variant: experimental-random-points/v1
  Anchor date: 2026-11-17 (calendar-extrapolated from 2018 EAGLE-I history)
  Record field: allocated_county_customer_impact
                (customer-equivalent weight per representative point,
                 aggregated to county level)
  experimental: true  — NOT an outage probability; raw impact proxy only.

Output format
-------------
  public/data/eaglei_impact.json
  {
    "counties": {
      "<fips_5digit>": <allocated_county_customer_impact_float>,
      ...
    }
  }

Deduplication: when a county FIPS appears in more than one predictions file,
keep the highest allocated_county_customer_impact value.
"""

import json
import os
import pathlib

# ── Paths ────────────────────────────────────────────────────────────────────

REPO_ROOT   = pathlib.Path(__file__).resolve().parents[2]
PRED_DIR    = REPO_ROOT / "power_outage_model" / "output" / "outage" / \
              "experimental-random-points" / "v1" / "predictions"
OUTPUT_FILE = REPO_ROOT / "public" / "data" / "eaglei_impact.json"

# ── Merge records across all prediction files ────────────────────────────────

counties: dict[str, float] = {}

pred_files = sorted(PRED_DIR.glob("*.json"))
if not pred_files:
    raise FileNotFoundError(f"No prediction JSON files found in {PRED_DIR}")

for path in pred_files:
    with open(path) as fh:
        payload = json.load(fh)
    for record in payload.get("records", []):
        fips   = record["county_fips"]
        impact = float(record["allocated_county_customer_impact"])
        # Keep the highest impact value across duplicate entries
        if fips not in counties or impact > counties[fips]:
            counties[fips] = impact

print(f"Merged {len(counties)} unique counties from {len(pred_files)} file(s).")

# ── Write output ─────────────────────────────────────────────────────────────

OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_FILE, "w") as fh:
    json.dump({"counties": counties}, fh, separators=(",", ":"))

print(f"Wrote {OUTPUT_FILE}")
