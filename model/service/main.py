"""
FastAPI read service.

Routes:
  GET /v1/forecasts/{county_fips}          → CountyForecast
  GET /v1/planned-outages/{county_fips}    → PlannedOutagesResponse
  GET /v1/coverage                         → list[CountyCoverage]
  GET /v1/weather-score                    → WeatherScoreResponse
  GET /healthz                             → {"status": "ok"}

County forecasts:
  - In fixture mode (USE_FIXTURES=true, the default), reads saved fixture JSON and
    sanitises it: forces data_mode=sample, null probabilities/durations, and
    status=insufficient_data to prevent misleading claims.
  - In live mode (USE_FIXTURES=false), returns data_mode=unavailable /
    status=insufficient_data with null numerical claims until a real probability
    backend is wired.  The unfinished DB branch is never reached in production.

Weather score:
  - Always fetches from Open-Meteo (wind speed, temperature, precipitation) and
    NWS active alerts — no fixture fallback in live mode.
  - Implements weather_score_v1 heuristic: not an outage probability.
  - Shares results across all clients via WeatherCache (GCS + in-process LRU).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

# Match Vite's local development env files. Explicit process variables take
# precedence, followed by .env.local, then .env.development.local.
_repo_root = Path(__file__).resolve().parents[2]
load_dotenv(_repo_root / ".env.local", override=False)
load_dotenv(_repo_root / ".env.development.local", override=False)

# Allow running from model/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.types import (
    AlertStatus,
    AlertsContext,
    CountyCoverage,
    CountyForecast,
    DataMode,
    ForecastStatus,
    ForecastWindow,
    NWSAlertItem,
    PlannedOutageNotice,
    PlannedOutagesResponse,
    PrimaryDriver,
    RefreshState,
    WeatherDriver,
    WeatherScoreMethod,
    WeatherScoreResponse,
    WeatherWindow,
)
from service.weather_cache import (
    WeatherCache,
    RefreshResult,
    normalise_coord,
    WEATHER_TTL_SECONDS,
    ALERT_TTL_SECONDS,
    WEATHER_STALE_MAX_SECONDS,
    ALERT_STALE_MAX_SECONDS,
    CacheEntry,
)

logger = logging.getLogger(__name__)
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

app = FastAPI(
    title="Runway Forecast API",
    version="1.0.0",
    description=(
        "Serves county-level unplanned power-outage forecasts and live weather scores. "
        "Numeric outage probabilities are only returned for counties that have passed "
        "the release gates described in model-deployment-plan.md."
    ),
)

# CORS is intentionally removed: production uses same-origin requests and
# development uses the Vite proxy.  Do not re-add a wildcard allow-origins.

# ---------------------------------------------------------------------------
# Lazy-initialised weather cache (bucket from WEATHER_CACHE_BUCKET env var)
# ---------------------------------------------------------------------------

_weather_cache: Optional[WeatherCache] = None


def _get_weather_cache() -> WeatherCache:
    global _weather_cache
    if _weather_cache is None:
        _weather_cache = WeatherCache()
    return _weather_cache


# ---------------------------------------------------------------------------
# /v1/plan  — request / response models
# ---------------------------------------------------------------------------

class PlanResident(BaseModel):
    name: str
    age: int | None = None
    devices: list[str] = []
    batteryWh: float | None = None
    isRenter: bool = False
    livesAlone: bool = False
    hasEV: bool = False
    language: str = "en"

class PlanForecast(BaseModel):
    # probability is now Optional; None means no calibrated estimate available
    probability: float | None = None
    windowStart: str = ""
    windowEnd: str = ""
    durationLowHours: float | None = None
    durationHighHours: float | None = None
    primaryCause: str = ""
    reasons: list[str] = []

class PlanRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    resident: PlanResident
    forecast: PlanForecast
    runwayHoursAtFullCharge: float | None = None
    nearestCoolPlace: str = ""

WhenLiteral = Literal["two_days_before", "night_before", "one_hour_before", "during_outage"]

class PlanItem(BaseModel):
    title: str
    title_en: str
    when: WhenLiteral
    reason: str
    reason_en: str

class PlanResponse(BaseModel):
    reply: str
    items: list[PlanItem]
    source: Literal["llm", "fallback"] = "llm"
    fallback_reason: Literal["missing_credentials", "project_unavailable", "runtime_unavailable", "model_unavailable", "access_denied", "watsonx_error"] | None = None


class WatsonxConfigurationError(ValueError):
    """Required watsonx credentials are missing."""


# ---------------------------------------------------------------------------
# /v1/plan  — watsonx.ai helper
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a household power-outage planning assistant. Respond to the resident's latest message.
Use the resident profile and forecast as background, but focus on what they just asked or told you.
Do not repeat unrelated actions from the profile. Do not invent facts, prices, battery run times,
nearby facilities, or outage predictions. Do not give medical treatment advice, dosing or diagnosis;
for device or medicine storage specifics, direct the resident to their supplier or pharmacist.
For an insulin fridge, suggest reviewing the medicine's storage instructions and asking a pharmacist
about backup cooling. Do not assume a safe storage duration or temperature, suggest moving an
appliance to a public facility, or promise that a battery or cooler will keep medicine safe.
Write a brief, direct "reply" in plain language, without a greeting or salutation. Answer the
latest message with a useful next step when possible. If the message is outside power-outage planning,
briefly explain what you can help with. Suggest 0 to 3 new plan items only when useful for the
latest message; an empty items array is valid. If a backup battery is known to cover fewer hours
than the expected outage, include a second power source or place to go when relevant.
Return only JSON matching the schema. Write "reply", "title" and "reason" in the resident's
language code, and provide English copies of item titles and reasons.
When the language code is "en", every string in the response must be in English.
Make every plan item title an action the resident can take, starting with a verb. Do not put a
number, duration, price, or place anywhere in the response unless it was given in the latest message
or forecast. Do not compare outage length with battery runtime unless both values are provided.
Do not claim you scheduled, contacted, booked, ordered, or charged anything; you can only suggest
steps and add them to the plan.
Only say you added a step to the plan if the "items" array contains that step.

Schema:
{
  "reply": "string",
  "items": [
    {
      "title": "string",
      "title_en": "string",
      "when": "two_days_before | night_before | one_hour_before | during_outage",
      "reason": "string",
      "reason_en": "string"
    }
  ]
}
"""


def _build_fallback(
    req: PlanRequest,
    reason: Literal["missing_credentials", "project_unavailable", "runtime_unavailable", "model_unavailable", "access_denied", "watsonx_error"],
) -> PlanResponse:
    """Offer only templates relevant to the latest message when the LLM is unavailable."""
    templates: dict = json.loads((FIXTURES_DIR / "plan_templates.json").read_text())
    items: list[PlanItem] = []
    message = req.message.lower()
    topics = {
        "insulin_fridge": ("insulin",),
        "oxygen_concentrator": ("oxygen", "concentrator"),
        "cpap": ("cpap",),
        "home_dialysis": ("dialysis",),
        "power_wheelchair": ("wheelchair",),
    }
    for device_key, keywords in topics.items():
        if not any(word in message for word in keywords):
            continue
        for t in templates.get(device_key, []):
            items.append(PlanItem(
                title=t["title"], title_en=t["title"],
                when=t.get("when", "night_before"),
                reason=t["reason"], reason_en=t["reason"],
            ))

    if not items and any(word in message for word in ("outage", "prepare", "power", "plan")):
        for t in templates.get("general", [])[:2]:
            items.append(PlanItem(
                title=t["title"], title_en=t["title"],
                when=t.get("when", "night_before"),
                reason=t["reason"], reason_en=t["reason"],
            ))

    reply = (
        "I can't answer that personally while watsonx is unavailable. "
        "These are general preparation steps for what you mentioned."
        if items else
        "I can't answer that personally while watsonx is unavailable. "
        "Please try again when the service is connected."
    )
    return PlanResponse(reply=reply, items=items[:3], source="fallback", fallback_reason=reason)


def _filter_generated_items(req: PlanRequest, items: list[PlanItem]) -> list[PlanItem]:
    """Discard plan claims with unsupported quantities or unsafe insulin destinations."""
    allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", req.message))
    for value in (
        req.forecast.durationLowHours,
        req.forecast.durationHighHours,
        req.runwayHoursAtFullCharge,
    ):
        if value is not None:
            allowed_numbers.add(str(value).removesuffix(".0"))

    filtered: list[PlanItem] = []
    for item in items:
        copy = " ".join((item.title, item.title_en, item.reason, item.reason_en))
        if set(re.findall(r"\d+(?:\.\d+)?", copy)) - allowed_numbers:
            continue
        if "insulin" in req.message.lower() and re.search(r"cooling center|public facilit", copy, re.I):
            continue
        if re.search(r"\b(?:ensures?|guarantees?)\b", copy, re.I) and re.search(
            r"\b(?:oxygen|concentrator|cpap|dialysis|wheelchair|insulin|medicine|medical device)\b", copy, re.I
        ):
            continue
        filtered.append(item)
    return filtered[:3]


def _filter_generated_reply(req: PlanRequest, reply: str) -> str:
    """Remove sentences with unsupported quantities or outage/runtime comparisons."""
    allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", req.message))
    for value in (req.forecast.durationLowHours, req.forecast.durationHighHours, req.runwayHoursAtFullCharge):
        if value is not None:
            allowed_numbers.add(str(value).removesuffix(".0"))

    duration_known = req.forecast.durationLowHours is not None or req.forecast.durationHighHours is not None
    runtime_known = req.runwayHoursAtFullCharge is not None
    safe_sentences: list[str] = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+", reply.strip()):
        if set(re.findall(r"\d+(?:\.\d+)?", sentence)) - allowed_numbers:
            continue
        if re.search(r"\b(?:ensures?|guarantees?)\b", sentence, re.I) and re.search(
            r"\b(?:oxygen|concentrator|cpap|dialysis|wheelchair|insulin|medicine|medical device)\b", sentence, re.I
        ):
            continue
        if re.search(r"\b(?:I(?:'|')ll|I will|we(?:'|')ll|we will)\s+(?:schedule|contact|book|reserve|order|charge)\b", sentence, re.I):
            continue
        if not (duration_known and runtime_known) and re.search(
            r"(?:outage|power).*(?:exceed|longer than|outlast).*(?:battery|backup)"
            r"|(?:battery|backup).*(?:shorter than|less than).*(?:outage|power)",
            sentence,
            re.I,
        ):
            continue
        safe_sentences.append(sentence)

    return " ".join(safe_sentences) or "Review the suggested steps and check your device instructions before relying on backup power."


def _align_reply_with_items(reply: str, items: list[PlanItem]) -> str:
    """Avoid saying a plan step was added when none survived validation."""
    if not items and re.search(r"\b(?:add(?:ed)?|put|includ(?:e|ed))\b.{0,50}\b(?:plan|step|item)\b", reply, re.I):
        return "I did not add a new step. Check your current plan for a related action, or tell me what else you need to prepare for."
    return reply


async def _call_watsonx(req: PlanRequest) -> PlanResponse:
    """Call watsonx.ai chat completions and parse the JSON response."""
    api_key = os.getenv("WATSONX_API_KEY", "")
    project_id = os.getenv("WATSONX_PROJECT_ID", "")
    region = os.getenv("WATSONX_REGION", "us-south")
    model_id = os.getenv("WATSONX_MODEL_ID", "ibm/granite-4-h-small")

    if not api_key or not project_id:
        raise WatsonxConfigurationError("WATSONX_API_KEY and WATSONX_PROJECT_ID must be set")

    # Obtain IAM token
    iam_url = "https://iam.cloud.ibm.com/identity/token"
    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            iam_url,
            data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": api_key},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_resp.raise_for_status()
        iam_token = token_resp.json()["access_token"]

        user_message = json.dumps({
            "latest_message": req.message,
            "recent_conversation": req.history[-6:],
            "resident": req.resident.model_dump(),
            "forecast": req.forecast.model_dump(),
            "runwayHoursAtFullCharge": req.runwayHoursAtFullCharge,
            "nearestCoolPlace": req.nearestCoolPlace,
        }, indent=2)

        wx_url = (
            f"https://{region}.ml.cloud.ibm.com/ml/v1/text/chat"
            "?version=2024-05-01"
        )
        payload = {
            "model_id": model_id,
            "project_id": project_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "parameters": {"max_new_tokens": 1024, "temperature": 0},
        }
        wx_resp = await client.post(
            wx_url,
            json=payload,
            headers={"Authorization": f"Bearer {iam_token}", "Content-Type": "application/json"},
            timeout=60,
        )
        wx_resp.raise_for_status()

    raw_text: str = wx_resp.json()["choices"][0]["message"]["content"]

    # Strip markdown fences if the model wraps its output
    if "```" in raw_text:
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    parsed = json.loads(raw_text)
    response = PlanResponse(**parsed, source="llm")
    response.items = _filter_generated_items(req, response.items)
    response.reply = _align_reply_with_items(_filter_generated_reply(req, response.reply), response.items)
    return response


# ---------------------------------------------------------------------------
# Fixture / DB helpers
# ---------------------------------------------------------------------------

def _null_windows(fips: str, now: datetime) -> tuple[ForecastWindow, ForecastWindow, ForecastWindow]:
    """Return three placeholder windows with null probability/duration."""
    w24 = ForecastWindow(
        window_label="Next 24h",
        window_start=now,
        window_end=now + timedelta(hours=24),
    )
    w48 = ForecastWindow(
        window_label="24–48h",
        window_start=now + timedelta(hours=24),
        window_end=now + timedelta(hours=48),
    )
    w72 = ForecastWindow(
        window_label="48–72h",
        window_start=now + timedelta(hours=48),
        window_end=now + timedelta(hours=72),
    )
    return w24, w48, w72


def _sanitise_fixture(fc: CountyForecast) -> CountyForecast:
    """
    Force sample provenance on any fixture-loaded forecast regardless of what
    the saved JSON claims.  Null out probability and duration to prevent
    misleading 'validated' or 'fresh' displays of August 2024 fixture data.
    Preserve original timestamps.
    """
    for window in (fc.next_24h, fc.next_24_48h, fc.next_48_72h):
        window.probability = None
        window.duration_low_hours = None
        window.duration_high_hours = None

    fc.data_mode = DataMode.SAMPLE
    fc.status = ForecastStatus.INSUFFICIENT_DATA
    fc.is_stale = False
    fc.stale_reason = None
    return fc


def _load_fixture_forecast(fips: str) -> CountyForecast | None:
    """Load a saved forecast fixture for dev/CI and sanitise its provenance."""
    path = FIXTURES_DIR / "forecasts" / f"{fips}.json"
    if path.exists():
        with open(path) as f:
            fc = CountyForecast.model_validate(json.load(f))
        return _sanitise_fixture(fc)
    # Fallback: generic stub
    stub_path = FIXTURES_DIR / "forecast_stub.json"
    if stub_path.exists():
        with open(stub_path) as f:
            data = json.load(f)
        data["county_fips"] = fips
        fc = CountyForecast.model_validate(data)
        return _sanitise_fixture(fc)
    return None


def _get_forecast(fips: str) -> CountyForecast:
    if len(fips) != 5 or not fips.isdigit():
        raise HTTPException(status_code=422, detail="county_fips must be exactly 5 digits")

    use_fixtures = os.getenv("USE_FIXTURES", "true").lower() == "true"
    if use_fixtures:
        fc = _load_fixture_forecast(fips)
        if fc is None:
            # Unknown county: return unavailable with null claims (never fabricate)
            now = datetime.now(timezone.utc)
            w24, w48, w72 = _null_windows(fips, now)
            return CountyForecast(
                county_fips=fips,
                county_name="Unknown",
                state="--",
                model_version="0.0.0-stub",
                issued_at=now,
                status=ForecastStatus.UNAVAILABLE,
                data_mode=DataMode.UNAVAILABLE,
                next_24h=w24,
                next_24_48h=w48,
                next_48_72h=w72,
            )
        return fc

    # Live mode: probability backend not yet implemented.
    # Return unavailable with null numerical claims rather than reaching the
    # unfinished database branch.
    now = datetime.now(timezone.utc)
    w24, w48, w72 = _null_windows(fips, now)
    return CountyForecast(
        county_fips=fips,
        county_name="Unknown",
        state="--",
        model_version="0.0.0-no-backend",
        issued_at=now,
        status=ForecastStatus.INSUFFICIENT_DATA,
        data_mode=DataMode.UNAVAILABLE,
        next_24h=w24,
        next_24_48h=w48,
        next_48_72h=w72,
    )


# ---------------------------------------------------------------------------
# Weather score — scoring logic
# ---------------------------------------------------------------------------

def _ramp(x: float, a: float, b: float) -> float:
    """Clamp((x-a)/(b-a), 0, 1)."""
    if b == a:
        return 0.0
    return max(0.0, min(1.0, (x - a) / (b - a)))


def _compute_weather_score_v1(
    wind_speeds: list[float],
    precip_values: list[float],
    temperatures: list[float],
) -> Optional[float]:
    """
    weather_score_v1 for one 24-hour window.

    Inputs must each have exactly 24 valid, complete hourly values.
    Returns None if fewer than 24 records are provided.

    wind  = ramp(max wind_speed m/s, 10, 25)
    rain  = ramp(mean precip mm,     1, 10)
    heat  = ramp(max temp °C,       30, 45)
    cold  = 1 − ramp(min temp °C,  −15,  0)
    score = 100 × (0.50×wind + 0.30×rain + 0.20×max(heat, cold))
    """
    if len(wind_speeds) < 24 or len(precip_values) < 24 or len(temperatures) < 24:
        return None

    wind = _ramp(max(wind_speeds), 10.0, 25.0)
    rain = _ramp(sum(precip_values) / len(precip_values), 1.0, 10.0)
    heat = _ramp(max(temperatures), 30.0, 45.0)
    cold = 1.0 - _ramp(min(temperatures), -15.0, 0.0)
    return round(100.0 * (0.50 * wind + 0.30 * rain + 0.20 * max(heat, cold)), 2)


def _build_drivers(
    wind_speeds: list[float],
    precip_values: list[float],
    temperatures: list[float],
) -> list[WeatherDriver]:
    """Return up to 3 numerical driver explanations for a window."""
    drivers: list[tuple[float, WeatherDriver]] = []

    wind_component = _ramp(max(wind_speeds), 10.0, 25.0)
    rain_component = _ramp(sum(precip_values) / len(precip_values), 1.0, 10.0)
    heat = _ramp(max(temperatures), 30.0, 45.0)
    cold = 1.0 - _ramp(min(temperatures), -15.0, 0.0)

    drivers.append((0.50 * wind_component, WeatherDriver(
        label="Peak wind speed",
        value=round(max(wind_speeds), 1),
        unit="m/s",
    )))
    drivers.append((0.30 * rain_component, WeatherDriver(
        label="Mean precipitation",
        value=round(sum(precip_values) / len(precip_values), 2),
        unit="mm/h",
    )))
    temp_driver = max(heat, cold)
    if temp_driver == heat:
        drivers.append((0.20 * heat, WeatherDriver(
            label="Maximum temperature",
            value=round(max(temperatures), 1),
            unit="°C",
        )))
    else:
        drivers.append((0.20 * cold, WeatherDriver(
            label="Minimum temperature",
            value=round(min(temperatures), 1),
            unit="°C",
        )))

    # Sort by contribution descending, keep top 3
    drivers.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in drivers[:3]]


# ---------------------------------------------------------------------------
# Weather score — provider fetch helpers
# ---------------------------------------------------------------------------

_NWS_USER_AGENT = "RunwayWeatherService/1.0 (noncommercial research; github.com/Serena20003/see_your_power)"
# Open-Meteo free API; no key required; noncommercial research use
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"

# 8-second per-attempt timeout, one retry per source
_PROVIDER_ATTEMPT_TIMEOUT = 8.0


def _validate_hourly_value(v: object, name: str) -> float:
    """Raise ValueError for null, non-finite, or domain-invalid values."""
    if v is None:
        raise ValueError(f"{name}: null value")
    f = float(v)  # type: ignore[arg-type]
    if not math.isfinite(f):
        raise ValueError(f"{name}: non-finite value {f!r}")
    return f


def _validate_precip(v: object) -> float:
    f = _validate_hourly_value(v, "precipitation")
    if f < 0:
        raise ValueError(f"precipitation: negative value {f!r}")
    return f


def _validate_wind(v: object) -> float:
    f = _validate_hourly_value(v, "wind_speed_10m")
    if f < 0:
        raise ValueError(f"wind_speed_10m: negative value {f!r}")
    return f


async def _fetch_open_meteo(lat: float, lon: float, client: httpx.AsyncClient) -> dict:
    """
    Fetch 76 hours of hourly temperature, wind speed, and precipitation from Open-Meteo.
    Returns the raw parsed JSON dict.
    Raises httpx.HTTPError or ValueError on failure.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,precipitation",
        "wind_speed_unit": "ms",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "timezone": "UTC",
        "forecast_hours": 76,
        "cell_selection": "land",
    }
    resp = await client.get(_OPEN_METEO_URL, params=params, timeout=_PROVIDER_ATTEMPT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


async def _fetch_nws_alerts(lat: float, lon: float, client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch active NWS alerts near (lat, lon).
    Returns a list of alert feature dicts (may be empty).
    Raises httpx.HTTPError on failure.
    """
    params = {"point": f"{lat},{lon}"}
    headers = {"User-Agent": _NWS_USER_AGENT, "Accept": "application/geo+json"}
    resp = await client.get(
        _NWS_ALERTS_URL, params=params, headers=headers, timeout=_PROVIDER_ATTEMPT_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("features", [])


def _parse_open_meteo(payload: dict, anchor: datetime) -> list[tuple[list[float], list[float], list[float]]]:
    """
    Parse Open-Meteo response into three 24-window tuples of
    (wind_speeds, precip_values, temperatures).

    anchor: UTC ceiling hour at which the first window starts.
    Precipitation is stamped at t+1h (end of interval); we assign it to [t, t+1h).
    Wind/temperature are endpoint values.
    Returns list of 3 tuples, each with 24 values.  Missing/incomplete windows
    are represented as tuples of fewer than 24 values.
    """
    hourly = payload.get("hourly", {})
    times_raw: list[str] = hourly.get("time", [])
    winds_raw: list = hourly.get("wind_speed_10m", [])
    precips_raw: list = hourly.get("precipitation", [])
    temps_raw: list = hourly.get("temperature_2m", [])

    if not times_raw:
        return [([], [], []), ([], [], []), ([], [], [])]

    # Build a map from time string → index
    time_index: dict[str, int] = {t: i for i, t in enumerate(times_raw)}

    # Detect and reject duplicate time entries
    if len(time_index) != len(times_raw):
        raise ValueError("Open-Meteo response contains duplicate time entries")

    def _get_hour(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M")

    result: list[tuple[list[float], list[float], list[float]]] = []
    for window_idx in range(3):
        w_winds: list[float] = []
        w_precips: list[float] = []
        w_temps: list[float] = []
        window_start = anchor + timedelta(hours=window_idx * 24)
        for h in range(24):
            t = window_start + timedelta(hours=h)
            t_key = _get_hour(t)
            t_next_key = _get_hour(t + timedelta(hours=1))

            # Wind and temperature: endpoint at t+1h
            wind_idx = time_index.get(t_next_key)
            temp_idx = time_index.get(t_next_key)
            # Precipitation: stamped at t+1h (end of [t, t+1h) interval)
            precip_idx = time_index.get(t_next_key)

            if wind_idx is None or precip_idx is None or temp_idx is None:
                # Incomplete window — stop collecting
                break

            try:
                w_winds.append(_validate_wind(winds_raw[wind_idx] if wind_idx < len(winds_raw) else None))
                w_precips.append(_validate_precip(precips_raw[precip_idx] if precip_idx < len(precips_raw) else None))
                w_temps.append(_validate_hourly_value(temps_raw[temp_idx] if temp_idx < len(temps_raw) else None, "temperature_2m"))
            except ValueError as exc:
                logger.warning("Rejecting invalid Open-Meteo value at %s: %s", t_key, exc)
                break

        result.append((w_winds, w_precips, w_temps))

    return result


def _parse_nws_alerts(features: list[dict]) -> list[NWSAlertItem]:
    """Extract NWSAlertItem instances from NWS GeoJSON alert features."""
    items: list[NWSAlertItem] = []
    now = datetime.now(timezone.utc)
    for feat in features:
        props = feat.get("properties", {})
        alert_id = props.get("id") or feat.get("id") or ""
        if not alert_id:
            continue
        expires_raw = props.get("expires")
        expires: Optional[datetime] = None
        if expires_raw:
            try:
                expires = datetime.fromisoformat(expires_raw)
            except ValueError:
                pass
        # Skip already-expired alerts
        if expires and expires < now:
            continue
        effective_raw = props.get("effective")
        onset_raw = props.get("onset")
        effective: Optional[datetime] = None
        onset: Optional[datetime] = None
        if effective_raw:
            try:
                effective = datetime.fromisoformat(effective_raw)
            except ValueError:
                pass
        if onset_raw:
            try:
                onset = datetime.fromisoformat(onset_raw)
            except ValueError:
                pass
        url = props.get("@id") or props.get("web")
        items.append(NWSAlertItem(
            id=str(alert_id),
            event=props.get("event", ""),
            effective=effective,
            onset=onset,
            expires=expires,
            url=url,
        ))
    return items


# ---------------------------------------------------------------------------
# Weather score — provider refresh function (passed to cache)
# ---------------------------------------------------------------------------

async def _weather_refresh_fn(lat: float, lon: float) -> RefreshResult:
    """
    Fetch Open-Meteo and NWS for (lat, lon).  Called by WeatherCache.
    Returns a RefreshResult.  Never raises; failures are reported via ok flags.
    """
    weather_payload: Optional[dict] = None
    alerts_payload: Optional[dict] = None
    weather_ok = False
    alerts_ok = False

    async with httpx.AsyncClient(
        headers={"Accept": "application/json"},
        timeout=_PROVIDER_ATTEMPT_TIMEOUT,
    ) as client:
        # Open-Meteo: up to 2 attempts
        for attempt in range(2):
            try:
                weather_payload = await _fetch_open_meteo(lat, lon, client)
                weather_ok = True
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Open-Meteo rate-limited; not retrying")
                    break
                if attempt == 0:
                    logger.warning("Open-Meteo attempt 1 failed (%s); retrying", exc)
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 0:
                    logger.warning("Open-Meteo attempt 1 failed (%s); retrying", exc)
                else:
                    logger.error("Open-Meteo failed after 2 attempts: %s", exc)

        # NWS: up to 2 attempts
        for attempt in range(2):
            try:
                features = await _fetch_nws_alerts(lat, lon, client)
                alerts_payload = {"features": features}
                alerts_ok = True
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("NWS rate-limited; not retrying")
                    break
                if attempt == 0:
                    logger.warning("NWS attempt 1 failed (%s); retrying", exc)
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 0:
                    logger.warning("NWS attempt 1 failed (%s); retrying", exc)
                else:
                    logger.error("NWS failed after 2 attempts: %s", exc)

    return RefreshResult(
        weather_payload=weather_payload,
        alerts_payload=alerts_payload,
        weather_ok=weather_ok,
        alerts_ok=alerts_ok,
    )


# ---------------------------------------------------------------------------
# Weather score — build response from cache entry
# ---------------------------------------------------------------------------

def _build_weather_response(
    requested_lat: float,
    requested_lon: float,
    q_lat: float,
    q_lon: float,
    entry: CacheEntry,
) -> WeatherScoreResponse:
    """
    Construct a WeatherScoreResponse from a resolved CacheEntry.
    Scores are suppressed for data older than 6 hours.
    """
    now = datetime.now(timezone.utc)

    # --- Weather freshness ---
    weather_age = entry.weather_age()
    weather_available = (
        entry.weather_payload is not None
        and weather_age is not None
        and weather_age < WEATHER_STALE_MAX_SECONDS
    )
    weather_stale = (
        weather_available
        and weather_age is not None
        and weather_age >= WEATHER_TTL_SECONDS
    )
    data_mode = DataMode.LIVE if weather_available else DataMode.UNAVAILABLE
    weather_retrieved_at: Optional[datetime] = None
    if entry.weather_retrieved_at is not None:
        weather_retrieved_at = datetime.fromtimestamp(entry.weather_retrieved_at, tz=timezone.utc)

    # --- Alerts freshness ---
    alert_age = entry.alerts_age()
    if entry.alerts_payload is not None and alert_age is not None:
        if alert_age < ALERT_TTL_SECONDS:
            alert_status = AlertStatus.AVAILABLE
        elif alert_age < ALERT_STALE_MAX_SECONDS:
            alert_status = AlertStatus.STALE
        else:
            alert_status = AlertStatus.UNAVAILABLE
    else:
        alert_status = AlertStatus.UNAVAILABLE

    alerts_retrieved_at: Optional[datetime] = None
    if entry.alerts_retrieved_at is not None:
        alerts_retrieved_at = datetime.fromtimestamp(entry.alerts_retrieved_at, tz=timezone.utc)

    # Build alert items (hide expired ones)
    alert_items: list[NWSAlertItem] = []
    if alert_status in (AlertStatus.AVAILABLE, AlertStatus.STALE) and entry.alerts_payload:
        try:
            alert_items = _parse_nws_alerts(entry.alerts_payload.get("features", []))
        except Exception as exc:
            logger.warning("Failed to parse NWS alerts: %s", exc)

    # --- Refresh state ---
    if entry.lease_active():
        refresh_state = RefreshState.UPDATING
        next_refresh_at = now + timedelta(seconds=5)
    elif entry.retry_blocked() and entry.next_retry_eligible is not None:
        refresh_state = RefreshState.RETRY_WAIT
        next_refresh_at = datetime.fromtimestamp(entry.next_retry_eligible, tz=timezone.utc)
    else:
        refresh_state = RefreshState.IDLE
        # Next refresh is at the earliest source expiry
        candidates: list[float] = []
        if entry.weather_retrieved_at is not None:
            candidates.append(entry.weather_retrieved_at + WEATHER_TTL_SECONDS)
        if entry.alerts_retrieved_at is not None:
            candidates.append(entry.alerts_retrieved_at + ALERT_TTL_SECONDS)
        if candidates:
            next_refresh_at = datetime.fromtimestamp(min(candidates), tz=timezone.utc)
        else:
            next_refresh_at = now + timedelta(seconds=30)

    # --- Windows ---
    windows: list[WeatherWindow] = []
    grid_lat: Optional[float] = None
    grid_lon: Optional[float] = None
    grid_elevation_m: Optional[float] = None

    if weather_available and entry.weather_payload:
        payload = entry.weather_payload
        grid_lat = payload.get("latitude")
        grid_lon = payload.get("longitude")
        grid_elevation_m = payload.get("elevation")

        # Anchor: UTC ceiling hour of weather retrieval
        if entry.weather_retrieved_at is not None:
            retrieved_dt = datetime.fromtimestamp(entry.weather_retrieved_at, tz=timezone.utc)
            if retrieved_dt.minute == 0 and retrieved_dt.second == 0:
                anchor = retrieved_dt
            else:
                anchor = retrieved_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            anchor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        try:
            window_data = _parse_open_meteo(payload, anchor)
        except ValueError as exc:
            logger.error("Open-Meteo parse error: %s", exc)
            window_data = [([], [], []), ([], [], []), ([], [], [])]

        for idx, (winds, precips, temps) in enumerate(window_data):
            w_start = anchor + timedelta(hours=idx * 24)
            w_end = w_start + timedelta(hours=24)
            score = _compute_weather_score_v1(winds, precips, temps) if len(winds) >= 24 else None
            drivers = _build_drivers(winds, precips, temps) if len(winds) >= 24 else []
            windows.append(WeatherWindow(
                window_start=w_start,
                window_end=w_end,
                score=score,
                peak_wind_speed_ms=round(max(winds), 2) if winds else None,
                min_temp_c=round(min(temps), 1) if temps else None,
                max_temp_c=round(max(temps), 1) if temps else None,
                mean_precip_mm=round(sum(precips) / len(precips), 3) if precips else None,
                drivers=drivers,
            ))

    return WeatherScoreResponse(
        requested_lat=requested_lat,
        requested_lon=requested_lon,
        queried_lat=q_lat,
        queried_lon=q_lon,
        grid_lat=grid_lat,
        grid_lon=grid_lon,
        grid_elevation_m=grid_elevation_m,
        method=WeatherScoreMethod.V1,
        data_mode=data_mode,
        calibrated=False,
        weather_retrieved_at=weather_retrieved_at,
        provider_issue_time=None,
        weather_available=weather_available,
        weather_stale=weather_stale,
        refresh_state=refresh_state,
        next_refresh_at=next_refresh_at,
        windows=windows,
        alerts=AlertsContext(
            status=alert_status,
            retrieved_at=alerts_retrieved_at,
            items=alert_items,
        ),
    )


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

    In this release all responses carry data_mode=sample (fixture mode) or
    data_mode=unavailable (live mode).  Numeric probability and duration are
    always null; no calibrated model backend is yet wired.

    - `status: insufficient_data` — release gates not met; probability = null.
    - `status: unavailable` — county not in coverage; no forecast.
    - `data_mode: sample` — data from a saved fixture; never fresh or validated.
    - `data_mode: unavailable` — no usable data for this county.
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


@app.get("/v1/weather-score", response_model=WeatherScoreResponse)
async def get_weather_score(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude of the selected location"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude of the selected location"),
) -> WeatherScoreResponse:
    """
    Live weather stress score for a selected point (0–100 scale).

    **This is an experimental weather indicator, not an outage probability.**

    Uses Open-Meteo for wind speed, precipitation, and temperature, and NWS for
    active alerts.  Results are shared across all callers at the same normalised
    location.  A fresh-cache read returns immediately; an expired cache refreshes
    inline (up to ~25 seconds on cold start).

    Coordinates are normalised to 3 decimal places (~110 m latitude spacing).
    Provider grid cells may be kilometres away regardless of decimal precision.
    """
    q_lat = normalise_coord(lat)
    q_lon = normalise_coord(lon)

    cache = _get_weather_cache()
    entry = await cache.get_or_refresh(q_lat, q_lon, _weather_refresh_fn)
    return _build_weather_response(lat, lon, q_lat, q_lon, entry)


@app.post("/v1/plan", response_model=PlanResponse)
async def post_plan(req: PlanRequest) -> PlanResponse:
    """
    Generate a personalised outage preparation plan for one household.

    Calls watsonx.ai when credentials are configured; falls back to static
    templates from fixtures/plan_templates.json otherwise.

    `forecast.probability` may be null when no calibrated estimate is available.
    """
    try:
        return await _call_watsonx(req)
    except WatsonxConfigurationError:
        logger.info("watsonx credentials not set; using fallback templates")
        reason = "missing_credentials"
    except httpx.HTTPStatusError as exc:
        logger.warning("watsonx call failed (%s); using fallback templates", exc)
        try:
            error_code = exc.response.json().get("errors", [{}])[0].get("code")
        except (ValueError, IndexError, KeyError, TypeError):
            error_code = None
        if error_code == "no_associated_service_instance_error":
            reason = "runtime_unavailable"
        elif error_code == "model_not_supported":
            reason = "model_unavailable"
        elif exc.response.status_code == 404:
            reason = "project_unavailable"
        elif exc.response.status_code in (401, 403):
            reason = "access_denied"
        else:
            reason = "watsonx_error"
    except (httpx.HTTPError, ValueError, KeyError, ValidationError) as exc:
        logger.warning("watsonx call failed (%s); using fallback templates", exc)
        reason = "watsonx_error"

    return _build_fallback(req, reason)
