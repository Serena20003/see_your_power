#!/usr/bin/env python3
"""
Generate per-county forecast JSON fixtures used by the FastAPI service in
USE_FIXTURES=true mode.

Runs batch inference using the fixture GFS/NWS data and writes one JSON file
per county to fixtures/forecasts/{fips}.json.

Usage (from model/ directory):
    python -m scripts.generate_fixtures
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone

from inference.batch import run_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate_fixtures")

OUT_DIR = Path(__file__).parent.parent / "fixtures" / "forecasts"


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Pin issue_time to the start of the fixture GFS data so window slicing
    # finds hours inside [issue_time, issue_time+72h).
    # fixtures/gfs_sample.json starts at 2024-08-01T12:00:00Z.
    issue_time = datetime(2024, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    logger.info("Running batch inference (fixture mode, issue_time=%s)…", issue_time)
    forecasts = await run_batch(use_fixtures=True, issue_time=issue_time)

    if not forecasts:
        logger.error("Batch returned no forecasts.")
        return 1

    for fc in forecasts:
        path = OUT_DIR / f"{fc.county_fips}.json"
        with open(path, "w") as f:
            # model_dump produces JSON-serialisable dicts; mode="json" handles datetime → str
            json.dump(fc.model_dump(mode="json"), f, indent=2)
        logger.info(
            "Wrote %s  status=%-22s  next24h_p=%s",
            path.name,
            fc.status,
            f"{fc.next_24h.probability:.3f}" if fc.next_24h.probability is not None else "null",
        )

    logger.info("Done — %d fixture files in %s", len(forecasts), OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
