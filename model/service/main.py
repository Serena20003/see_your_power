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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


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
    probability: float
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
number, duration, price, or place in a plan item unless it was given in the latest message or forecast.

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
        filtered.append(item)
    return filtered[:3]


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
    return response


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


@app.post("/v1/plan", response_model=PlanResponse)
async def post_plan(req: PlanRequest) -> PlanResponse:
    """
    Generate a personalised outage preparation plan for one household.

    Calls watsonx.ai when credentials are configured; falls back to static
    templates from fixtures/plan_templates.json otherwise.
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
