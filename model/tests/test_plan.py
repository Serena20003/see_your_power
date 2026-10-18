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
    guaranteed_device = main.PlanItem(title="Charge backup battery", title_en="Charge backup battery", when="two_days_before", reason="Ensures your oxygen concentrator will run reliably.", reason_en="Ensures your oxygen concentrator will run reliably.")

    assert main._filter_generated_items(request, [safe, invented_time, public_facility, guaranteed_device]) == [safe]


def test_generated_reply_drops_unsupported_runtime_claims():
    request = plan_request("Please add a plan step to charge my backup battery before a power outage.")
    reply = (
        "Charge your backup battery today. "
        "Test it for 30 minutes. "
        "The expected outage may exceed the battery's runtime."
    )

    assert main._filter_generated_reply(request, reply) == "Charge your backup battery today."


def test_generated_reply_drops_claimed_real_world_actions():
    request = plan_request("Please add a step to charge my backup battery.")

    assert main._filter_generated_reply(request, "I'll schedule charging your battery. Check your device instructions.") == "Check your device instructions."


def test_generated_reply_drops_device_guarantees():
    request = plan_request("I use an oxygen concentrator.")

    assert main._filter_generated_reply(request, "A battery ensures your oxygen concentrator will run reliably. Check the device instructions.") == "Check the device instructions."


def test_reply_does_not_claim_an_unreturned_plan_step():
    assert main._align_reply_with_items("I've added a plan step for you.", []) == (
        "I did not add a new step. Check your current plan for a related action, or tell me what else you need to prepare for."
    )
