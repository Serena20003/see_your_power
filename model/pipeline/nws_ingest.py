"""
NWS active alerts ingestion.
https://api.weather.gov/alerts/active?point={lat},{lon}
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from .features import NWSAlert

logger = logging.getLogger(__name__)
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


async def fetch_alerts(
    county_fips: str,
    lat: float,
    lon: float,
    use_fixtures: bool = False,
) -> list[NWSAlert]:
    if use_fixtures or os.getenv("USE_FIXTURES", "false").lower() == "true":
        return _load_fixture(county_fips)

    url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": "runway-forecast/1.0"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("NWS alerts fetch failed for %s: %s; using fixture", county_fips, exc)
            return _load_fixture(county_fips)

    alerts: list[NWSAlert] = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        onset_str = props.get("onset") or props.get("effective")
        expires_str = props.get("expires") or props.get("ends")
        if not onset_str or not expires_str:
            continue
        try:
            alerts.append(
                NWSAlert(
                    county_fips=county_fips,
                    event=props.get("event", ""),
                    onset=datetime.fromisoformat(onset_str),
                    expires=datetime.fromisoformat(expires_str),
                )
            )
        except ValueError:
            continue
    return alerts


def _load_fixture(county_fips: str) -> list[NWSAlert]:
    fixture_path = FIXTURES_DIR / "nws_alerts_sample.json"
    if not fixture_path.exists():
        return []
    with open(fixture_path) as f:
        raw = json.load(f)
    alerts = []
    for entry in raw:
        try:
            alerts.append(
                NWSAlert(
                    county_fips=county_fips,
                    event=entry["event"],
                    onset=datetime.fromisoformat(entry["onset"]),
                    expires=datetime.fromisoformat(entry["expires"]),
                )
            )
        except (KeyError, ValueError):
            continue
    return alerts
