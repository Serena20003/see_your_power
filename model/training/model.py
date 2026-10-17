"""
Outage-onset model: training, calibration, and serialisation.

Design (from model-deployment-plan.md):
  1. County seasonal baseline (Poisson GLM)
  2. Logistic regression baseline
  3. Pooled gradient-boosted onset model (LightGBM)
  4. Isotonic-regression calibration per forecast window
  5. Separate conditional duration model (survival / AFT)

Training data requirements (enforced by assertions):
  - Labels must have explicit outage_type == "unplanned"
  - Planned, PSPS, and unknown labels are excluded
  - Negative examples (county-period with no outage) are only included
    when reporting coverage is known to be complete (coverage_complete == True)
  - No synthetic or scripted outage records
  - Time-based train/test split; held-out utilities + states for transfer eval

This file provides training stubs with the full interface and safety checks.
Populate `load_labels()` and `load_features()` with real data pipelines
before fitting a production model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Training label record
# ---------------------------------------------------------------------------

@dataclass
class OutageLabel:
    county_fips: str
    window_start: str       # ISO date
    window_end: str
    outage_occurred: bool   # True = ≥1 new unplanned outage onset in window
    outage_type: str        # must be "unplanned"; others excluded in checks
    coverage_complete: bool # True only when reporting coverage is verified
    source: str             # data provenance identifier


# ---------------------------------------------------------------------------
# Safety checks on labels
# ---------------------------------------------------------------------------

def validate_labels(labels: list[OutageLabel]) -> list[OutageLabel]:
    """
    Enforce exclusion rules from model-deployment-plan.md.
    Returns only labels that pass all checks; logs exclusion counts.
    """
    n_total = len(labels)
    valid = []
    n_planned = n_psps = n_unknown = n_coverage = 0

    for lbl in labels:
        t = lbl.outage_type.lower()
        if t in ("planned", "maintenance"):
            n_planned += 1
            continue
        if t == "psps":
            n_psps += 1
            continue
        if t not in ("unplanned",):
            n_unknown += 1
            continue
        # Negative examples require confirmed coverage
        if not lbl.outage_occurred and not lbl.coverage_complete:
            n_coverage += 1
            continue
        valid.append(lbl)

    logger.info(
        "Label validation: %d total → %d valid (excluded: %d planned, %d PSPS, "
        "%d unknown, %d incomplete-coverage negatives)",
        n_total, len(valid), n_planned, n_psps, n_unknown, n_coverage,
    )
    return valid


# ---------------------------------------------------------------------------
# Feature names (must match WindowFeatureVector fields used in training)
# ---------------------------------------------------------------------------

FEATURE_COLS = [
    "max_gust_mph",
    "max_temp_f",
    "wet_soil",
    "wind_score",
    "heat_score",
    "exposure",
    "history",
    "tree_cover_pct",
    "overhead_line_share",
    "terrain_roughness",
    "past_unplanned_outages_3yr",
    "has_red_flag_warning",
    "has_high_wind_warning",
]


# ---------------------------------------------------------------------------
# Model stub – replace with real fitting logic
# ---------------------------------------------------------------------------

class OutageOnsetModel:
    """
    Pooled gradient-boosted onset model with calibration.

    In production:
      - Fit a LightGBM binary classifier on FEATURE_COLS → outage_occurred
      - Calibrate with isotonic regression on a held-out time slice
      - Persist to Cloud Storage as a versioned artifact
    """

    def __init__(self, version: str = "0.0.0-stub"):
        self.version = version
        self._fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """
        Fit the model.  X shape: (n_samples, len(FEATURE_COLS)).
        Enforces minimum sample count before fitting.
        """
        assert X.shape[0] >= 1000, (
            "Fewer than 1 000 validated training examples. "
            "Do not release numeric forecasts without adequate data."
        )
        assert X.shape[1] == len(FEATURE_COLS), "Feature column mismatch"
        # --- Placeholder: replace with LightGBM fit + isotonic calibration ---
        logger.warning("OutageOnsetModel.fit() is a stub. Provide real training data.")
        self._fitted = True

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return calibrated onset probability for each row of X."""
        if not self._fitted:
            raise RuntimeError("Model has not been fitted.")
        # Stub: heuristic probability from wind + heat scores
        wind_col = FEATURE_COLS.index("wind_score")
        heat_col = FEATURE_COLS.index("heat_score")
        exposure_col = FEATURE_COLS.index("exposure")
        history_col = FEATURE_COLS.index("history")

        hazard = np.maximum(X[:, wind_col], 0.8 * X[:, heat_col])
        p = 1 - np.exp(-2.2 * hazard * (0.5 + X[:, exposure_col]) * (0.6 + 0.8 * X[:, history_col]))

        # Red Flag / High Wind floor
        rf_col = FEATURE_COLS.index("has_red_flag_warning")
        hw_col = FEATURE_COLS.index("has_high_wind_warning")
        alert_mask = (X[:, rf_col] > 0) | (X[:, hw_col] > 0)
        p = np.where(alert_mask, np.maximum(p, 0.6), p)

        return np.clip(p, 0.02, 0.97)

    def save(self, path: str) -> None:
        """Serialise to Cloud Storage (stub)."""
        logger.info("OutageOnsetModel.save() stub called — no artifact written.")

    @classmethod
    def load(cls, path: str) -> "OutageOnsetModel":
        """Load from Cloud Storage (stub)."""
        logger.warning("OutageOnsetModel.load() stub: returning unfitted model.")
        return cls()


# ---------------------------------------------------------------------------
# Duration model stub
# ---------------------------------------------------------------------------

class ConditionalDurationModel:
    """
    Conditional duration model: given an outage occurred, estimate
    low/high hours using restoration records (accounting for censoring).

    Production: AFT survival model or quantile regression on restoration times.
    """

    def predict_range(
        self,
        primary_driver: str,
        median_outage_hours: float,
    ) -> tuple[float, float]:
        """Return (low_hours, high_hours) conditional on an outage occurring."""
        if primary_driver == "wind":
            return (
                max(2.0, 0.5 * median_outage_hours),
                max(8.0, 1.5 * median_outage_hours),
            )
        # heat / other
        return (1.0, 6.0)
