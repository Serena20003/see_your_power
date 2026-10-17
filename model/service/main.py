"""
FastAPI read service.

Routes:
  GET /v1/forecasts/{county_fips}          → CountyForecast
  GET /v1/planned-outages/{county_fips}    → PlannedOutagesResponse
  GET /v1/coverage                         → list[CountyCoverage]
  GET /healthz                             → {"status": "ok"}

In production, forecasts are written to PostgreSQL by inference/batch.py
and served from there.  In dev/CI, this service reads from fixtures/.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Allow running from model/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.types import (
    CountyCoverage,
    CountyForecast,
    ForecastStatus,
    ForecastWindow,
    PlannedOutageNotice,
    PlannedOutagesResponse,
    PrimaryDriver,
)

logger = logging.getLogger(__name__)
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

app = FastAPI(
    title="Runway Forecast API",
    version="1.0.0",
    description=(
        "Serves county-level unplanned power-outage forecasts. "
        "Numeric probabilities are only returned for counties that have passed "
        "the release gates described in model-deployment-plan.md."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten in production
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Fixture / DB helpers
# ---------------------------------------------------------------------------

def _load_fixture_forecast(fips: str) -> CountyForecast | None:
    """Load a saved forecast fixture for dev/CI."""
    path = FIXTURES_DIR / "forecasts" / f"{fips}.json"
    if path.exists():
        with open(path) as f:
            return CountyForecast.model_validate(json.load(f))
    # Fallback: generic stub
    stub_path = FIXTURES_DIR / "forecast_stub.json"
    if stub_path.exists():
        with open(stub_path) as f:
            data = json.load(f)
        data["county_fips"] = fips
        return CountyForecast.model_validate(data)
    return None


def _get_forecast(fips: str) -> CountyForecast:
    if len(fips) != 5 or not fips.isdigit():
        raise HTTPException(status_code=422, detail="county_fips must be exactly 5 digits")

    use_fixtures = os.getenv("USE_FIXTURES", "true").lower() == "true"
    if use_fixtures:
        fc = _load_fixture_forecast(fips)
        if fc is None:
            # Return a well-formed "unavailable" response rather than 404
            now = datetime.now(timezone.utc)
            return CountyForecast(
                county_fips=fips,
                county_name="Unknown",
                state="--",
                model_version="0.0.0-stub",
                issued_at=now,
                status=ForecastStatus.UNAVAILABLE,
                next_24h=ForecastWindow(
                    window_label="Next 24h",
                    window_start=now,
                    window_end=now,
                ),
                next_24_48h=ForecastWindow(
                    window_label="24–48h",
                    window_start=now,
                    window_end=now,
                ),
                next_48_72h=ForecastWindow(
                    window_label="48–72h",
                    window_start=now,
                    window_end=now,
                ),
            )
        return fc

    # Production: query PostgreSQL
    raise HTTPException(status_code=503, detail="Database not configured")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/forecasts/{county_fips}", response_model=CountyForecast)
def get_forecast(county_fips: str) -> CountyForecast:
    """
    County-level unplanned outage forecast for the next 72 hours in three windows.

    - `status: local_validated` — trained on local history; numeric probability shown.
    - `status: transfer_estimate` — no local history; probability transferred from similar counties.
    - `status: insufficient_data` — release gates not met; probability = null.
    - `status: unavailable` — county not in coverage; no forecast.
    """
    return _get_forecast(county_fips)


@app.get("/v1/planned-outages/{county_fips}", response_model=PlannedOutagesResponse)
def get_planned_outages(county_fips: str) -> PlannedOutagesResponse:
    """
    Source-backed planned maintenance and PSPS notices for a county.
    This endpoint is outside the model pipeline; it never contributes to
    unplanned-outage probabilities.
    """
    if len(county_fips) != 5 or not county_fips.isdigit():
        raise HTTPException(status_code=422, detail="county_fips must be exactly 5 digits")

    now = datetime.now(timezone.utc)
    # Stub: return "no usable feed" for demo
    return PlannedOutagesResponse(
        county_fips=county_fips,
        retrieved_at=now,
        no_feed_reason="No public planned-outage feed available for this county.",
        notices=[],
    )


@app.get("/v1/coverage", response_model=list[CountyCoverage])
def get_coverage() -> list[CountyCoverage]:
    """County coverage metadata: which counties have local labels, transfer, or no data."""
    path = FIXTURES_DIR / "coverage.json"
    if path.exists():
        with open(path) as f:
            return [CountyCoverage.model_validate(item) for item in json.load(f)]
    return []
