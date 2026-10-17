#!/usr/bin/env python3
"""
CLI script: generate synthetic training data and fit the model stub.

Usage (from model/ directory):
    python -m training.generate_synthetic_data
    python -m training.generate_synthetic_data --samples 3000 --seed 7 --out data/

Output (written to --out directory, default: training/data/):
    synthetic_labels.csv      – label records (one row per county-window)
    synthetic_features.csv    – feature matrix aligned to FEATURE_COLS
    synthetic_full.csv        – merged frame with all columns + label

Exits non-zero if:
  - Fewer than 1 000 valid training labels are generated
  - Feature matrix shape does not match FEATURE_COLS
  - OutageOnsetModel.fit() raises (i.e. the stub assertion fires)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

# Allow running as `python training/generate_synthetic_data.py` from model/
sys.path.insert(0, str(Path(__file__).parent.parent))

from training.model import FEATURE_COLS, OutageOnsetModel
from training.synthetic_data import generate_training_set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate_synthetic_data")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate synthetic outage-risk training data."
    )
    parser.add_argument("--samples", type=int, default=2000,
                        help="Number of county-window samples to generate (default: 2000)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--out", type=str, default="",
                        help="Output directory (default: training/data/)")
    parser.add_argument("--no-fit", action="store_true",
                        help="Skip the model stub fit() smoke-test")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else Path(__file__).parent / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # 1. Generate
    # -----------------------------------------------------------------------
    logger.info("Generating %d synthetic samples (seed=%d)…", args.samples, args.seed)
    labels, X, df = generate_training_set(n_samples=args.samples, seed=args.seed)

    # -----------------------------------------------------------------------
    # 2. Basic sanity checks
    # -----------------------------------------------------------------------
    if len(labels) < 1000:
        logger.error(
            "Only %d valid labels generated — model requires ≥ 1 000. "
            "Increase --samples.",
            len(labels),
        )
        return 1

    if X.shape[1] != len(FEATURE_COLS):
        logger.error(
            "Feature matrix has %d columns but FEATURE_COLS has %d.",
            X.shape[1], len(FEATURE_COLS),
        )
        return 1

    pos_rate = df["outage_occurred"].mean() * 100
    logger.info(
        "Dataset stats: %d rows, %.1f%% positive, %d unique counties",
        len(df), pos_rate, df["county_fips"].nunique(),
    )

    # Print a per-feature summary
    feat_df = df[FEATURE_COLS]
    logger.info("Feature summary:\n%s", feat_df.describe().round(3).to_string())

    # -----------------------------------------------------------------------
    # 3. Smoke-test the model stub
    # -----------------------------------------------------------------------
    if not args.no_fit:
        y = df["outage_occurred"].to_numpy(dtype=np.float64)
        logger.info("Running OutageOnsetModel.fit() smoke test…")
        try:
            model = OutageOnsetModel(version="synthetic-v1")
            model.fit(X, y)
            probs = model.predict_proba(X[:10])
            logger.info(
                "predict_proba on first 10 rows → %s",
                np.round(probs, 3).tolist(),
            )
            logger.info("✓ Model stub fit + predict_proba passed.")
        except AssertionError as exc:
            logger.error("Model assertion failed: %s", exc)
            return 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error during model fit: %s", exc)
            return 1

    # -----------------------------------------------------------------------
    # 4. Write artifacts
    # -----------------------------------------------------------------------
    # Labels CSV
    label_cols = [
        "county_fips", "state", "window_start", "window_end",
        "outage_occurred", "outage_type", "coverage_complete", "source",
    ]
    labels_path = out_dir / "synthetic_labels.csv"
    df[label_cols].to_csv(labels_path, index=False)
    logger.info("Wrote %s", labels_path)

    # Feature matrix CSV
    features_path = out_dir / "synthetic_features.csv"
    df[["county_fips", "window_start"] + FEATURE_COLS].to_csv(features_path, index=False)
    logger.info("Wrote %s", features_path)

    # Full merged CSV (convenient for exploration)
    full_path = out_dir / "synthetic_full.csv"
    df.to_csv(full_path, index=False)
    logger.info("Wrote %s", full_path)

    logger.info(
        "Done. %d training-ready samples in %s/", len(df), out_dir
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
