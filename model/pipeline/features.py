"""
Feature engineering for the Runway outage-onset predictor.

This module ingests raw weather (NOAA GFS via AWS Open Data), NWS alerts,
and precomputed physical features (canopy, terrain) to produce a county-level
feature vector for each 24-hour forecast window.

Key design principles from model-deployment-plan.md:
  - GFS is published every 6 h; hourly re-issue reuses the latest available run.
  - All weather inputs are FORECAST values as they existed at issue time
    (no future leakage into training).
  - PSPS notices and planned-outage flags are NEVER features.
  - Negative training labels are only marked when coverage is known complete.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Raw GFS weather record (one county × one hour)
# ---------------------------------------------------------------------------

@dataclass
class GFSHour:
    valid_time: datetime
    county_fips: str
    wind_gust_mph: float
    temp_f: float
    # Soil moisture (m³/m³), used as wet-soil proxy
    soil_moisture: float
    precip_mm: float = 0.0


# ---------------------------------------------------------------------------
# NWS alert record
# ---------------------------------------------------------------------------

@dataclass
class NWSAlert:
    county_fips: str
    event: str          # e.g. "Red Flag Warning", "High Wind Warning"
    onset: datetime
    expires: datetime


# ---------------------------------------------------------------------------
# Pre-computed county physical features (loaded once from Cloud Storage / DB)
# ---------------------------------------------------------------------------

@dataclass
class CountyPhysicalFeatures:
    county_fips: str
    tree_cover_pct: float           # 0–100, USGS tree canopy
    overhead_line_share: float      # 0–1, share of distribution lines overhead
    terrain_roughness: float        # 0–1, stddev elevation / 100 (normalised)
    fire_zone: str = "none"         # "none" | "tier2" | "tier3" (CA only)
    past_unplanned_outages_3yr: int = 0
    median_unplanned_hours: float = 4.0
    # Coverage flags
    canopy_available: bool = True
    terrain_available: bool = True


# ---------------------------------------------------------------------------
# Computed feature vector (one county × one 24-hour window)
# ---------------------------------------------------------------------------

@dataclass
class WindowFeatureVector:
    county_fips: str
    window_start: datetime
    window_end: datetime

    # Weather-derived scalars
    max_gust_mph: float = 0.0
    max_temp_f: float = 60.0
    gust_hour: Optional[datetime] = None
    temp_hour: Optional[datetime] = None
    wet_soil: float = 0.0           # 0 or 1

    # Scores (0–1) following risk.ts algorithm for demo window
    wind_score: float = 0.0
    heat_score: float = 0.0

    # Physical exposure composite
    exposure: float = 0.0
    # Historical base rate (0–1)
    history: float = 0.0

    # Alert flags
    has_red_flag_warning: bool = False
    has_high_wind_warning: bool = False

    # Raw physical features (pass-through for model)
    tree_cover_pct: float = 0.0
    overhead_line_share: float = 0.0
    terrain_roughness: float = 0.0
    past_unplanned_outages_3yr: int = 0

    # Metadata
    gfs_run_time: Optional[datetime] = None
    feature_complete: bool = True    # False if any required input was missing


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def build_feature_vector(
    gfs_hours: list[GFSHour],
    alerts: list[NWSAlert],
    physical: CountyPhysicalFeatures,
    window_start: datetime,
    window_end: datetime,
) -> WindowFeatureVector:
    """
    Construct a WindowFeatureVector from raw inputs for a single county window.

    The algorithm mirrors the transparent heuristic in specs.md §8.1 but is
    extended with terrain roughness and used as feature engineering for the
    gradient-boosted model, not as the final probability estimate.
    """
    fips = physical.county_fips
    vec = WindowFeatureVector(
        county_fips=fips,
        window_start=window_start,
        window_end=window_end,
    )

    # --- Slice GFS to this window ----------------------------------------
    window_hours = [
        h for h in gfs_hours
        if window_start <= h.valid_time < window_end
    ]
    if not window_hours:
        logger.warning("No GFS hours for %s window %s–%s", fips, window_start, window_end)
        vec.feature_complete = False
        return vec

    gusts = [h.wind_gust_mph for h in window_hours]
    temps = [h.temp_f for h in window_hours]
    soils = [h.soil_moisture for h in window_hours]

    vec.max_gust_mph = max(gusts)
    vec.max_temp_f = max(temps)
    vec.gust_hour = window_hours[np.argmax(gusts)].valid_time
    vec.temp_hour = window_hours[np.argmax(temps)].valid_time
    vec.wet_soil = 1.0 if max(soils) > 0.30 else 0.0

    if vec.gfs_run_time is None and window_hours:
        # Record the GFS run time from the first hour (all hours share a run)
        vec.gfs_run_time = window_hours[0].valid_time

    # --- Scores (transparent heuristic, also used as features) -----------
    raw_wind = _clamp((vec.max_gust_mph - 25) / 35, 0.0, 1.0)
    vec.wind_score = _clamp(raw_wind * (1 + 0.3 * vec.wet_soil), 0.0, 1.0)
    vec.heat_score = _clamp((vec.max_temp_f - 90) / 20, 0.0, 1.0)

    # --- Exposure composite -----------------------------------------------
    fire_factor = {"none": 0.0, "tier2": 0.6, "tier3": 1.0}.get(
        physical.fire_zone, 0.0
    )
    vec.tree_cover_pct = physical.tree_cover_pct if physical.canopy_available else 0.0
    vec.overhead_line_share = physical.overhead_line_share
    vec.terrain_roughness = physical.terrain_roughness if physical.terrain_available else 0.0

    vec.exposure = (
        0.4 * (vec.tree_cover_pct / 100)
        + 0.3 * vec.overhead_line_share
        + 0.2 * fire_factor
        + 0.1 * vec.terrain_roughness
    )

    # --- History (unplanned-only) ----------------------------------------
    vec.past_unplanned_outages_3yr = physical.past_unplanned_outages_3yr
    vec.history = _clamp(physical.past_unplanned_outages_3yr / 6, 0.0, 1.0)

    # --- Alert flags -------------------------------------------------------
    active = [
        a for a in alerts
        if a.county_fips == fips and a.onset < window_end and a.expires > window_start
    ]
    for alert in active:
        ev = alert.event.lower()
        if "red flag" in ev:
            vec.has_red_flag_warning = True
        if "high wind" in ev:
            vec.has_high_wind_warning = True

    if not physical.canopy_available or not physical.terrain_available:
        vec.feature_complete = False

    return vec


def build_three_windows(
    gfs_hours: list[GFSHour],
    alerts: list[NWSAlert],
    physical: CountyPhysicalFeatures,
    issue_time: datetime,
) -> list[WindowFeatureVector]:
    """Build feature vectors for all three rolling 24-hour windows."""
    windows = [
        (issue_time, issue_time + timedelta(hours=24)),
        (issue_time + timedelta(hours=24), issue_time + timedelta(hours=48)),
        (issue_time + timedelta(hours=48), issue_time + timedelta(hours=72)),
    ]
    return [
        build_feature_vector(gfs_hours, alerts, physical, ws, we)
        for ws, we in windows
    ]
