"""Read-only serving of daily predictions from the pinned experimental model."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import logging
import math
import os
import threading
import time

import httpx
from fastapi import HTTPException
from shared.types import CountyForecast, ForecastWindow, ExperimentalImpactMetadata

MODEL_ID = '4e97b613a2ff4aebeb7d63d4140c397972c2c832983611ac099a7db2d0d0d572'
DEFAULT_URL = 'https://storage.googleapis.com/runway-515115-map-tiles/experimental-impact/latest.json'
logger = logging.getLogger(__name__)


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get('counties'), dict):
        raise ValueError('Experimental county export must be an object')
    if (data.get('schema_version') != 'experimental-impact-web/v1' or data.get('model_id') != MODEL_ID
            or data.get('fixture') is not False or data.get('experimental') is not True
            or data.get('physical_outage_probability') is not False
            or data.get('training_weather') is not False or data.get('training_canopy') is not False
            or data.get('locations_simulated') is not True or data.get('windows_are_fixed_utc_days') is not True):
        raise ValueError('Unexpected experimental model provenance')
    anchor = datetime.fromisoformat(data['anchor_date']).replace(tzinfo=timezone.utc)
    stamp = datetime.fromisoformat(data['published_at'])
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('Invalid publication time')
    supported = 0
    for fips, county in data['counties'].items():
        if len(fips) != 5 or not fips.isdigit():
            raise ValueError('Invalid county FIPS')
        if not isinstance(county, dict) or not isinstance(county.get('windows'), list):
            raise ValueError('Invalid county windows')
        windows = county['windows']
        if not windows:
            continue
        if len(windows) != 3:
            raise ValueError('Incomplete experimental forecast')
        supported += 1
        for i, window in enumerate(windows):
            value = window['estimated_customer_impact']
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid impact value')
            start = datetime.fromisoformat(window['start'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(window['end'].replace('Z', '+00:00'))
            if start != anchor + timedelta(days=i) or end != start + timedelta(days=1):
                raise ValueError('Invalid daily forecast windows')
    if not supported or supported != data['supported_counties']:
        raise ValueError('Experimental coverage count mismatch')
    if sorted(f for f, c in data['counties'].items() if not c['windows']) != data['unsupported_counties']:
        raise ValueError('Experimental unsupported county mismatch')
    return data


class ImpactCache:
    def __init__(self):
        self.data = None
        self.next_refresh = 0
        self.refresh_failed = False
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic() < self.next_refresh and self.data is not None:
                return self.data
            try:
                path = os.getenv('EXPERIMENTAL_IMPACT_PATH')
                if path:
                    data = json.loads(Path(path).read_bytes())
                else:
                    response = httpx.get(os.getenv('EXPERIMENTAL_IMPACT_URL', DEFAULT_URL), timeout=8)
                    response.raise_for_status()
                    data = response.json()
                data = validate(data)
                if self.data and data['anchor_date'] < self.data['anchor_date']:
                    raise ValueError('Experimental forecast rollback')
                self.data = data
                self.next_refresh = time.monotonic() + 300
                self.refresh_failed = False
            except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
                logger.warning('Experimental impact refresh failed: %s', type(exc).__name__)
                self.refresh_failed = True
                self.next_refresh = time.monotonic() + 30
                if self.data is None:
                    raise HTTPException(503, 'Experimental model estimates are temporarily unavailable.') from exc
            return self.data

    def forecast(self, fips):
        data = self.get()
        county = data['counties'].get(fips, {'name': 'Unsupported county', 'state': '--', 'windows': []})
        supported = len(county['windows']) == 3
        anchor = datetime.fromisoformat(data['anchor_date']).replace(tzinfo=timezone.utc)
        stale = datetime.now(timezone.utc) >= anchor + timedelta(days=1) or self.refresh_failed
        windows = []
        for i in range(3):
            row = county['windows'][i] if supported else None
            windows.append(ForecastWindow(window_label=f'Day {i+1} · UTC',
                window_start=anchor + timedelta(days=i), window_end=anchor + timedelta(days=i+1),
                estimated_customer_impact=row['estimated_customer_impact'] if row else None))
        return CountyForecast(county_fips=fips, county_name=county['name'], state=county['state'],
            model_version='experimental-impact-' + data['model_id'][:8], issued_at=data['published_at'],
            status='experimental' if supported else 'unavailable', data_mode='experimental',
            next_24h=windows[0], next_24_48h=windows[1], next_48_72h=windows[2],
            is_stale=stale, stale_reason='Daily model update is old or could not be refreshed.' if stale else None,
            experimental_impact=ExperimentalImpactMetadata(model_id=data['model_id'], anchor_date=data['anchor_date'],
                history_year=data['history_year'], supported=supported, supported_counties=data['supported_counties'],
                calendar_extrapolation=data['calendar_extrapolation'],
                within_training_feature_ranges=supported and all(w['within_training_feature_ranges'] for w in county['windows']),
                limitations=data['limitations']))


cache = ImpactCache()


def enabled():
    return os.getenv('EXPERIMENTAL_IMPACT_ENABLED', 'true').lower() == 'true'
