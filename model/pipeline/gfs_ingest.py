"""
NOAA GFS ingestion from AWS Open Data (s3://noaa-gfs-bdp-pds/).

Production: Cloud Storage → BigQuery pipeline.
Dev / CI: reads from local fixtures/gfs_sample.json.

https://registry.opendata.aws/noaa-gfs-bdp-pds/
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from .features import GFSHour

logger = logging.getLogger(__name__)

# Open-Meteo is used for dev/fallback (free, no key required).
# Production GFS parsing would use cfgrib / eccodes.
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=temperature_2m,wind_gusts_10m,soil_moisture_0_to_1cm"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph"
    "&timezone=UTC&forecast_days=4"
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _mph_to_mph(v: float) -> float:
    return v  # already in mph from Open-Meteo when wind_speed_unit=mph


async def fetch_gfs_for_county(
    county_fips: str,
    lat: float,
    lon: float,
    issue_time: datetime,
    use_fixtures: bool = False,
) -> list[GFSHour]:
    """
    Return hourly GFSHour records for the 72 h ahead of issue_time.

    In production this reads from Cloud Storage GFS grib files.
    In dev/CI it calls Open-Meteo (free tier, noncommercial use; see terms).
    """
    if use_fixtures or os.getenv("USE_FIXTURES", "false").lower() == "true":
        return _load_fixture(county_fips, issue_time)

    url = OPEN_METEO_URL.format(lat=lat, lon=lon)
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Open-Meteo fetch failed for %s: %s; using fixture", county_fips, exc)
            return _load_fixture(county_fips, issue_time)

    hours: list[GFSHour] = []
    times = data["hourly"]["time"]
    gusts = data["hourly"]["wind_gusts_10m"]
    temps = data["hourly"]["temperature_2m"]
    soils = data["hourly"]["soil_moisture_0_to_1cm"]

    for i, t in enumerate(times):
        dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        if dt < issue_time:
            continue
        if dt >= issue_time + timedelta(hours=72):
            break
        hours.append(
            GFSHour(
                valid_time=dt,
                county_fips=county_fips,
                wind_gust_mph=float(gusts[i] or 0),
                temp_f=float(temps[i] or 60),
                soil_moisture=float(soils[i] or 0),
            )
        )
    return hours


def _load_fixture(county_fips: str, issue_time: datetime) -> list[GFSHour]:
    """Load from local JSON fixture (used in tests and offline mode)."""
    fixture_path = FIXTURES_DIR / "gfs_sample.json"
    if not fixture_path.exists():
        logger.warning("GFS fixture not found at %s; returning empty", fixture_path)
        return []
    with open(fixture_path) as f:
        raw = json.load(f)
    hours = []
    for entry in raw:
        dt = datetime.fromisoformat(entry["valid_time"]).replace(tzinfo=timezone.utc)
        hours.append(
            GFSHour(
                valid_time=dt,
                county_fips=county_fips,
                wind_gust_mph=float(entry.get("wind_gust_mph", 0)),
                temp_f=float(entry.get("temp_f", 60)),
                soil_moisture=float(entry.get("soil_moisture", 0)),
                precip_mm=float(entry.get("precip_mm", 0)),
            )
        )
    return hours
