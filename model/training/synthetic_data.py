"""
Synthetic training data generator for the Runway outage-onset predictor.

Domain knowledge encoded here is grounded in publicly documented outage-risk
literature (NERC, CPUC, USFS fire-weather research):

  Wind events   – Gusts above ~35 mph combined with dry conditions and high
                  overhead-line share are the #1 cause of distribution outages
                  in the western US.  Above 55 mph the probability saturates
                  near 1 for counties with significant tree canopy.

  Heat events   – Sustained temps >100 °F drive transformer / cable failures.
                  Risk grows with overhead line share and terrain exposure.

  Fire-weather  – Red Flag Warnings correlate with ~3–5× baseline outage rate.
                  High Wind Warnings add another multiplicative boost.

  History / terrain – Counties with ≥4 unplanned outages in 3 years and high
                  terrain roughness have structurally elevated base rates.

  Wet soil      – Rain-soaked roots weaken trees, amplifying wind damage.
                  Wet soil after a dry spell is especially hazardous.

The generator produces:
  - list[OutageLabel]          – validated training labels
  - np.ndarray  (N × 13)       – matching feature matrix aligned to FEATURE_COLS
  - pd.DataFrame               – human-readable CSV-friendly frame

Negative examples are only marked when coverage_complete=True (mirrors the
production constraint enforced by validate_labels).

Usage:
    from training.synthetic_data import generate_training_set
    labels, X, df = generate_training_set(n_samples=2000, seed=42)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Tuple

import numpy as np
import pandas as pd

# Allow running from the model/ root without an installed package
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from training.model import FEATURE_COLS, OutageLabel, validate_labels

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# County archetypes — representative of real US distribution-grid diversity
# ---------------------------------------------------------------------------

COUNTY_ARCHETYPES = [
    # fips    name                  state  tree%  ovhd  rough  fire_zone  hist  med_h
    ("06037", "Los Angeles",        "CA",  18.0,  0.55, 0.28,  "tier2",   4,    5.5),
    ("06059", "Orange",             "CA",  12.0,  0.48, 0.22,  "tier2",   2,    4.0),
    ("06073", "San Diego",          "CA",  14.0,  0.50, 0.35,  "tier3",   5,    6.0),
    ("06065", "Riverside",          "CA",  10.0,  0.60, 0.40,  "tier3",   6,    7.0),
    ("06071", "San Bernardino",     "CA",   9.0,  0.62, 0.45,  "tier3",   7,    8.0),
    ("06083", "Santa Barbara",      "CA",  22.0,  0.52, 0.38,  "tier2",   3,    5.0),
    ("06111", "Ventura",            "CA",  20.0,  0.53, 0.32,  "tier2",   4,    5.5),
    ("06001", "Alameda",            "CA",  28.0,  0.45, 0.18,  "none",    2,    3.5),
    ("41051", "Multnomah (OR)",     "OR",  58.0,  0.40, 0.25,  "none",    3,    4.5),
    ("53033", "King (WA)",          "WA",  62.0,  0.38, 0.30,  "none",    4,    5.0),
    ("48113", "Dallas (TX)",        "TX",  24.0,  0.70, 0.08,  "none",    5,    4.0),
    ("48201", "Harris (TX)",        "TX",  20.0,  0.72, 0.06,  "none",    6,    4.5),
    ("12086", "Miami-Dade (FL)",    "FL",  30.0,  0.65, 0.02,  "none",    7,    3.0),
    ("12011", "Broward (FL)",       "FL",  28.0,  0.66, 0.02,  "none",    6,    3.0),
    ("36061", "New York (NY)",      "NY",  20.0,  0.30, 0.10,  "none",    2,    3.0),
    ("36081", "Queens (NY)",        "NY",  18.0,  0.32, 0.08,  "none",    2,    3.0),
    ("17031", "Cook (IL)",          "IL",  18.0,  0.55, 0.05,  "none",    3,    3.5),
    ("25025", "Suffolk (MA)",       "MA",  22.0,  0.58, 0.12,  "none",    3,    4.0),
    ("08031", "Denver (CO)",        "CO",  15.0,  0.50, 0.42,  "none",    2,    4.0),
    ("08059", "Jefferson (CO)",     "CO",  35.0,  0.55, 0.55,  "none",    4,    5.5),
]


def _fire_factor(zone: str) -> float:
    return {"none": 0.0, "tier2": 0.6, "tier3": 1.0}.get(zone, 0.0)


def _exposure(tree_pct: float, ovhd: float, fire_zone: str, roughness: float) -> float:
    return (
        0.4 * (tree_pct / 100)
        + 0.3 * ovhd
        + 0.2 * _fire_factor(fire_zone)
        + 0.1 * roughness
    )


def _history_score(past_3yr: int) -> float:
    return min(past_3yr / 6.0, 1.0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _outage_probability(
    wind_score: float,
    heat_score: float,
    wet_soil: float,
    exposure: float,
    history: float,
    has_rfwarn: float,
    has_hwwarn: float,
) -> float:
    """
    Physics-informed probability function used to label synthetic samples.

    Mirrors the domain understanding in features.py and model.py while adding
    interaction terms documented in outage-risk literature:
      - Wet soil × wind (root-weakening effect)
      - Fire-zone alert multiplier (Red Flag Warning)
      - Saturating history base rate
    """
    # Primary hazard: wind dominates, heat secondary
    hazard = _clamp(wind_score + 0.5 * heat_score + 0.15 * wet_soil * wind_score, 0.0, 1.5)

    # Infrastructure vulnerability
    vuln = 0.5 + exposure + 0.4 * history

    # Poisson-like onset probability
    p = 1.0 - np.exp(-1.8 * hazard * vuln)

    # Alert multipliers
    if has_rfwarn:
        p = _clamp(p * 2.2, 0.0, 1.0)
    if has_hwwarn:
        p = _clamp(p * 1.6, 0.0, 1.0)

    # Structural floor: even calm days have ~2% chance in high-history counties
    floor = 0.02 + 0.03 * history
    return _clamp(p, floor, 0.97)


def generate_training_set(
    n_samples: int = 2000,
    seed: int = 42,
    start_date: date = date(2021, 1, 1),
    end_date: date = date(2024, 6, 30),
    positive_oversample: float = 1.5,
) -> Tuple[list[OutageLabel], np.ndarray, pd.DataFrame]:
    """
    Generate a synthetic training set for OutageOnsetModel.

    Parameters
    ----------
    n_samples : int
        Total number of county-window samples to generate (>= 1000 required by model).
    seed : int
        Random seed for reproducibility.
    start_date / end_date : date
        Date range from which window_start dates are sampled.
    positive_oversample : float
        Multiplier applied to true-positive probability threshold to ensure
        ~30–40% positive rate (mirrors real-world moderate imbalance).

    Returns
    -------
    labels : list[OutageLabel]
        Validated OutageLabel records (all outage_type="unplanned", coverage_complete=True).
    X : np.ndarray
        Feature matrix of shape (n_samples, 13) aligned to FEATURE_COLS.
    df : pd.DataFrame
        Human-readable DataFrame with all columns including label.
    """
    rng = np.random.default_rng(seed)
    date_range_days = (end_date - start_date).days

    rows = []
    for _ in range(n_samples):
        # --- Sample a county archetype ---
        arch = COUNTY_ARCHETYPES[rng.integers(len(COUNTY_ARCHETYPES))]
        fips, _, state, tree_pct, ovhd, roughness, fire_zone, hist_3yr, _ = arch

        # --- Sample a date / window ---
        day_offset = int(rng.integers(date_range_days - 1))
        ws = start_date + timedelta(days=day_offset)
        we = ws + timedelta(days=1)

        # Season-aware weather sampling (CA dry season: May–Oct)
        month = ws.month
        dry_season = state == "CA" and 5 <= month <= 10

        # Wind gust (mph) — log-normal; elevated in dry season and WA/OR winter
        wind_season_boost = 1.4 if dry_season else (1.2 if state in ("WA", "OR") and month in (11, 12, 1, 2) else 1.0)
        max_gust = float(rng.lognormal(mean=3.1, sigma=0.55) * wind_season_boost)
        max_gust = _clamp(max_gust, 2.0, 90.0)

        # Temperature (°F)
        base_temp = {
            "CA": 75, "OR": 60, "WA": 58, "TX": 82,
            "FL": 85, "NY": 62, "IL": 65, "MA": 60,
            "CO": 65,
        }.get(state, 68)
        seasonal_temp = base_temp + 15 * np.sin((month - 3) * np.pi / 6)
        max_temp = float(rng.normal(seasonal_temp, 12))
        max_temp = _clamp(max_temp, 20.0, 118.0)

        # Soil moisture — wetter in winter/spring for CA; generally wetter in PNW
        if state == "CA":
            soil_base = 0.30 if month in (12, 1, 2, 3) else 0.08
        elif state in ("WA", "OR"):
            soil_base = 0.35 if month in (10, 11, 12, 1, 2, 3) else 0.15
        else:
            soil_base = 0.20
        soil_moisture = float(_clamp(rng.normal(soil_base, 0.08), 0.0, 0.60))
        wet_soil = 1.0 if soil_moisture > 0.30 else 0.0

        # --- Derived scores (mirror features.py exactly) ---
        raw_wind = _clamp((max_gust - 25) / 35, 0.0, 1.0)
        wind_score = _clamp(raw_wind * (1 + 0.3 * wet_soil), 0.0, 1.0)
        heat_score = _clamp((max_temp - 90) / 20, 0.0, 1.0)

        exp = _exposure(tree_pct, ovhd, fire_zone, roughness)
        hist = _history_score(hist_3yr)

        # --- Alert flags (correlated with weather) ---
        # Red Flag Warning: high wind + low humidity proxy (dry season, gust > 35)
        rfwarn_prob = 0.0
        if dry_season and max_gust > 35:
            rfwarn_prob = _clamp((max_gust - 35) / 30, 0.0, 0.85)
        elif state in ("CO",) and max_gust > 40:
            rfwarn_prob = 0.4
        has_rfwarn = float(rng.random() < rfwarn_prob)

        # High Wind Warning: gust > 55 mph or strong storm system
        hwwarn_prob = _clamp((max_gust - 45) / 25, 0.0, 0.90)
        has_hwwarn = float(rng.random() < hwwarn_prob)

        # --- Label via physics-informed probability ---
        p_true = _outage_probability(
            wind_score, heat_score, wet_soil, exp, hist,
            has_rfwarn, has_hwwarn,
        )
        # Apply mild oversample to increase positive rate toward ~35%
        p_draw = _clamp(p_true * positive_oversample, 0.0, 0.97)
        outage_occurred = bool(rng.random() < p_draw)

        rows.append({
            "county_fips":              fips,
            "state":                    state,
            "window_start":             ws.isoformat(),
            "window_end":               we.isoformat(),
            # label
            "outage_occurred":          outage_occurred,
            "outage_type":              "unplanned",
            "coverage_complete":        True,
            "source":                   "synthetic_v1",
            # features (in FEATURE_COLS order)
            "max_gust_mph":             round(max_gust, 2),
            "max_temp_f":               round(max_temp, 2),
            "wet_soil":                 wet_soil,
            "wind_score":               round(wind_score, 4),
            "heat_score":               round(heat_score, 4),
            "exposure":                 round(exp, 4),
            "history":                  round(hist, 4),
            "tree_cover_pct":           tree_pct,
            "overhead_line_share":      ovhd,
            "terrain_roughness":        roughness,
            "past_unplanned_outages_3yr": hist_3yr,
            "has_red_flag_warning":     has_rfwarn,
            "has_high_wind_warning":    has_hwwarn,
        })

    df = pd.DataFrame(rows)

    # --- Build OutageLabel list and validate ---
    labels = [
        OutageLabel(
            county_fips=r["county_fips"],
            window_start=r["window_start"],
            window_end=r["window_end"],
            outage_occurred=bool(r["outage_occurred"]),
            outage_type=r["outage_type"],
            coverage_complete=bool(r["coverage_complete"]),
            source=r["source"],
        )
        for r in rows
    ]
    labels = validate_labels(labels)

    # --- Build feature matrix aligned to FEATURE_COLS ---
    X = df[FEATURE_COLS].to_numpy(dtype=np.float64)

    pos_rate = df["outage_occurred"].mean()
    logger.info(
        "Synthetic dataset: %d samples, %.1f%% positive, %d counties, date range %s–%s",
        len(df), pos_rate * 100, df["county_fips"].nunique(), start_date, end_date,
    )

    return labels, X, df
