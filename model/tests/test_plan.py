"""Regression checks for message-aware assistant responses."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from service import main


def plan_request(message: str) -> main.PlanRequest:
    return main.PlanRequest(
        message=message,
        resident=main.PlanResident(name="Test", devices=["oxygen_concentrator", "insulin_fridge"]),
        forecast=main.PlanForecast(probability=0.5),
    )


def test_watsonx_receives_latest_message(monkeypatch):
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "iam.cloud.ibm.com":
            return httpx.Response(200, json={"access_token": "test-token"})
        seen.append(json.loads(request.content)["messages"][-1]["content"])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({"reply": "Charge your phone now.", "items": []})}}]
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setenv("WATSONX_API_KEY", "test-key")
    monkeypatch.setenv("WATSONX_PROJECT_ID", "test-project")

    result = asyncio.run(main._call_watsonx(plan_request("How do I charge my phone?")))

    assert result.reply == "Charge your phone now."
    assert json.loads(seen[0])["latest_message"] == "How do I charge my phone?"


def test_fallback_uses_message_instead_of_all_profile_devices():
    insulin = main._build_fallback(plan_request("I have an insulin fridge"), "missing_credentials")
    unrelated = main._build_fallback(plan_request("What is my favorite color?"), "missing_credentials")

    assert insulin.items
    assert all("oxygen" not in item.title.lower() for item in insulin.items)
    assert unrelated.items == []


def test_generated_items_drop_unsupported_medical_claims():
    request = plan_request("I have an insulin fridge")
    safe = main.PlanItem(title="Ask your pharmacist", title_en="Ask your pharmacist", when="two_days_before", reason="Confirm backup cooling options.", reason_en="Confirm backup cooling options.")
    invented_time = main.PlanItem(title="Move insulin after 12 hours", title_en="Move insulin after 12 hours", when="during_outage", reason="The outage may be long.", reason_en="The outage may be long.")
    public_facility = main.PlanItem(title="Visit a cooling center", title_en="Visit a cooling center", when="during_outage", reason="Take the fridge there.", reason_en="Take the fridge there.")

    assert main._filter_generated_items(request, [safe, invented_time, public_facility]) == [safe]
