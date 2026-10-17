"""
Batch inference: read latest GFS + alerts, run feature engineering,
apply the onset model, assemble CountyForecast objects, and write
results atomically to PostgreSQL / Cloud Storage.

Cloud Scheduler invokes this job hourly via Cloud Run.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from pipeline.features import (
    CountyPhysicalFeatures,
    WindowFeatureVector,
    build_three_windows,
)
from pipeline.gfs_ingest import fetch_gfs_for_county
from pipeline.nws_ingest import fetch_alerts
from shared.types import (
    CountyForecast,
    ForecastStatus,
    ForecastWindow,
    PrimaryDriver,
)
from training.model import (
    FEATURE_COLS,
    ConditionalDurationModel,
    OutageOnsetModel,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = os.getenv("MODEL_VERSION", "0.0.0-stub")
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# County registry (production: loaded from PostgreSQL / Cloud Storage)
# ---------------------------------------------------------------------------

def load_county_registry() -> list[dict]:
    """
    Returns a list of county dicts with fips, name, state, lat, lon,
    physical features, and label-coverage metadata.

    In production this reads from a DB/GCS artifact.
    In dev/CI it reads from fixtures/counties.json.
    """
    fixture = FIXTURES_DIR / "counties.json"
    if fixture.exists():
        with open(fixture) as f:
            return json.load(f)
    # Minimal stub: just Los Angeles County for offline testing
    return [
        {
            "fips": "06037",
            "name": "Los Angeles",
            "state": "CA",
            "lat": 34.052235,
            "lon": -118.243683,
            "tree_cover_pct": 18.0,
            "overhead_line_share": 0.55,
            "terrain_roughness": 0.3,
            "fire_zone": "tier2",
            "past_unplanned_outages_3yr": 4,
            "median_unplanned_hours": 5.5,
            "canopy_available": True,
            "terrain_available": True,
            "has_local_labels": False,   # gates numeric release
        }
    ]


# ---------------------------------------------------------------------------
# Feature vector → numpy row
# ---------------------------------------------------------------------------

def vec_to_row(v: WindowFeatureVector) -> np.ndarray:
    return np.array([
        [
            v.max_gust_mph,
            v.max_temp_f,
            v.wet_soil,
            v.wind_score,
            v.heat_score,
            v.exposure,
            v.history,
            v.tree_cover_pct,
            v.overhead_line_share,
            v.terrain_roughness,
            float(v.past_unplanned_outages_3yr),
            float(v.has_red_flag_warning),
            float(v.has_high_wind_warning),
        ]
    ], dtype=float)


# ---------------------------------------------------------------------------
# Reason builder (mirrors specs.md §8.1 step 15)
# ---------------------------------------------------------------------------

def build_reasons(v: WindowFeatureVector) -> list[str]:
    candidates: list[tuple[float, str]] = []
    if v.wind_score > 0:
        candidates.append((v.wind_score, f"Wind gusts up to {v.max_gust_mph:.0f} mph"))
    if v.wet_soil and v.wind_score > 0:
        candidates.append((0.3 * v.wet_soil * v.wind_score, "Ground is wet, so trees fall more easily"))
    if v.heat_score > 0:
        candidates.append((v.heat_score, f"Heat up to {v.max_temp_f:.0f}°F will strain the grid"))
    if v.tree_cover_pct > 0:
        candidates.append((0.4 * v.tree_cover_pct / 100, f"{v.tree_cover_pct:.0f}% tree cover near power lines"))
    if v.overhead_line_share > 0.3:
        candidates.append((0.3 * v.overhead_line_share, "Mostly overhead power lines"))
    if v.past_unplanned_outages_3yr > 0:
        candidates.append((v.history, f"{v.past_unplanned_outages_3yr} unplanned outages here in the last 3 years"))
    if v.has_red_flag_warning:
        candidates.append((0.8, "Red Flag Warning in effect"))
    if v.has_high_wind_warning:
        candidates.append((0.8, "High Wind Warning in effect"))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in candidates[:4]]


# ---------------------------------------------------------------------------
# Determine forecast status (release gate)
# ---------------------------------------------------------------------------

def _forecast_status(county: dict) -> ForecastStatus:
    if not county.get("has_local_labels"):
        return ForecastStatus.INSUFFICIENT_DATA
    return ForecastStatus.LOCAL_VALIDATED


# ---------------------------------------------------------------------------
# Single county forecast
# ---------------------------------------------------------------------------

async def forecast_county(
    county: dict,
    model: OutageOnsetModel,
    duration_model: ConditionalDurationModel,
    issue_time: datetime,
    use_fixtures: bool = False,
) -> CountyForecast:
    fips = county["fips"]
    status = _forecast_status(county)

    physical = CountyPhysicalFeatures(
        county_fips=fips,
        tree_cover_pct=county.get("tree_cover_pct", 0.0),
        overhead_line_share=county.get("overhead_line_share", 0.0),
        terrain_roughness=county.get("terrain_roughness", 0.0),
        fire_zone=county.get("fire_zone", "none"),
        past_unplanned_outages_3yr=county.get("past_unplanned_outages_3yr", 0),
        median_unplanned_hours=county.get("median_unplanned_hours", 4.0),
        canopy_available=county.get("canopy_available", True),
        terrain_available=county.get("terrain_available", True),
    )

    gfs_hours = await fetch_gfs_for_county(
        fips, county["lat"], county["lon"], issue_time, use_fixtures
    )
    alerts = await fetch_alerts(fips, county["lat"], county["lon"], use_fixtures)

    window_labels = ["Next 24h", "24–48h", "48–72h"]
    feature_vecs = build_three_windows(gfs_hours, alerts, physical, issue_time)

    window_forecasts: list[ForecastWindow] = []
    for label, vec in zip(window_labels, feature_vecs):
        if status == ForecastStatus.INSUFFICIENT_DATA or not vec.feature_complete:
            wf = ForecastWindow(
                window_label=label,
                window_start=vec.window_start,
                window_end=vec.window_end,
                probability=None,
            )
        else:
            row = vec_to_row(vec)
            prob = float(model.predict_proba(row)[0])

            primary = (
                PrimaryDriver.WIND if vec.wind_score >= 0.8 * vec.heat_score
                else PrimaryDriver.HEAT
            )
            dur_low, dur_high = duration_model.predict_range(
                primary.value, physical.median_unplanned_hours
            )
            reasons = build_reasons(vec)
            wf = ForecastWindow(
                window_label=label,
                window_start=vec.window_start,
                window_end=vec.window_end,
                probability=prob,
                duration_low_hours=dur_low,
                duration_high_hours=dur_high,
                reasons=reasons,
                primary_driver=primary,
                active_alerts=(["Red Flag Warning"] if vec.has_red_flag_warning else [])
                + (["High Wind Warning"] if vec.has_high_wind_warning else []),
            )
        window_forecasts.append(wf)

    max_gust = max((h.wind_gust_mph for h in gfs_hours), default=None)
    max_temp = max((h.temp_f for h in gfs_hours), default=None)

    return CountyForecast(
        county_fips=fips,
        county_name=county["name"],
        state=county["state"],
        model_version=MODEL_VERSION,
        issued_at=issue_time,
        source_timestamps={"gfs": issue_time, "nws": issue_time},
        status=status,
        next_24h=window_forecasts[0],
        next_24_48h=window_forecasts[1],
        next_48_72h=window_forecasts[2],
        max_gust_mph=max_gust,
        max_temp_f=max_temp,
    )


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

async def run_batch(use_fixtures: bool = False, issue_time: datetime | None = None) -> list[CountyForecast]:
    if issue_time is None:
        issue_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    counties = load_county_registry()
    model = OutageOnsetModel(version=MODEL_VERSION)

    # In production: load from Cloud Storage artifact.
    # model = OutageOnsetModel.load("gs://runway-models/onset/latest.pkl")
    #
    # In dev/fixture mode, fit the stub on synthetic data so predict_proba works.
    if not model._fitted:
        try:
            from training.synthetic_data import generate_training_set
            _, X, df = generate_training_set(n_samples=2000, seed=42)
            y = df["outage_occurred"].to_numpy(dtype=np.float64)
            model.fit(X, y)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not auto-fit model stub: %s — probabilities will use heuristic.", exc)

    duration_model = ConditionalDurationModel()
    logger.info("Starting batch inference for %d counties at %s", len(counties), issue_time)

    tasks = [
        forecast_county(county, model, duration_model, issue_time, use_fixtures)
        for county in counties
    ]
    forecasts = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[CountyForecast] = []
    for county, result in zip(counties, forecasts):
        if isinstance(result, Exception):
            logger.error("Forecast failed for %s: %s", county["fips"], result)
        else:
            results.append(result)

    logger.info("Batch complete: %d/%d counties succeeded", len(results), len(counties))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = asyncio.run(run_batch(use_fixtures=True))
    for fc in results:
        print(
            f"{fc.county_fips} {fc.county_name}, {fc.state} | "
            f"status={fc.status} | "
            f"next24h_p={fc.next_24h.probability}"
        )
