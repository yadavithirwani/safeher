"""Companion tests use real ADK orchestration with a scripted model; no external keys."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.genai import types

from safeher.api import app
from safeher.chatbot import agents, tools
from safeher.chatbot.maps import MapsClient, MapsUnavailable
from safeher.chatbot.models import ChatRequest, Position, is_urgent, location_warnings
from safeher.chatbot.service import ChatService


class ScriptModel(BaseLlm):
    model: str = "script"
    steps: list = []

    async def generate_content_async(self, llm_request, stream=False):
        step = self.steps.pop(0)
        if isinstance(step, tuple):
            name, args = step
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        else:
            part = types.Part(text=step)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def run(coro):
    return asyncio.run(coro)


def scripted_service(monkeypatch, steps):
    model = ScriptModel(steps=steps)
    monkeypatch.setattr(agents, "model", lambda: model)
    return ChatService(lambda sessions: Runner(app_name="safeher_companion",
        agent=agents.build_agent_team(), session_service=sessions))


def test_team_structure_and_tool_schemas():
    from google.adk.tools import FunctionTool
    team = agents.build_agent_team()
    assert [a.name for a in team.sub_agents] == ["pre_trip", "during_trip", "local_enquiry"]
    for agent in team.sub_agents:
        for function in agent.tools:
            assert FunctionTool(function)._get_declaration().name == function.__name__


@pytest.mark.parametrize("message", ["SOS", "I don't feel safe", "someone is following me", "मदद चाहिए"])
def test_emergency_never_initializes_runner(message):
    def forbidden(_):
        pytest.fail("Emergency called the model")
    result = run(ChatService(forbidden).chat(ChatRequest(message=message)))
    assert result.urgency == "urgent"
    assert result.actions[0].value == "tel:112"
    assert result.session_id is None


def test_sos_api_and_security_headers():
    response = TestClient(app).post("/api/chat", json={"message": "help", "sos": True})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["urgency"] == "urgent"


def test_validation():
    client = TestClient(app)
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x", "location": {
        "lat": 200, "lon": 1, "accuracy_m": 1, "observed_at": datetime.now(timezone.utc).isoformat()}}).status_code == 422


def test_adk_transfer_and_clarification(monkeypatch):
    service = scripted_service(monkeypatch, [
        ("transfer_to_agent", {"agent_name": "local_enquiry"}),
        ("ask_clarification", {"topic": "location"}), "An invented pharmacy is around the corner."])
    result = run(service.chat(ChatRequest(message="Where's the nearest pharmacy?")))
    assert result.needs_clarification
    assert "which location" in result.reply
    assert "invented" not in result.reply
    assert result.session_id


def test_adk_search_uses_real_tool_evidence(monkeypatch):
    async def nearby(self, kind, lat, lon):
        return {"places": [{"id": "test", "name": "Fixture pharmacy", "lat": lat, "lon": lon,
            "kind": kind, "source": "test_fixture", "distance_m": 90,
            "distance_basis": "straight_line", "hours_basis": "unknown"}]}
    monkeypatch.setattr(MapsClient, "nearby", nearby)
    service = scripted_service(monkeypatch, [
        ("transfer_to_agent", {"agent_name": "local_enquiry"}),
        ("search_nearby_places", {"kind": "pharmacy", "lat": 28.54, "lon": 77.2}),
        "This is the safest pharmacy, guaranteed."])
    result = run(service.chat(ChatRequest(message="Pharmacy near 28.54, 77.2")))
    assert result.places[0]["name"] == "Fixture pharmacy"
    assert "straight-line" in result.reply
    assert "guaranteed" not in result.reply
    assert result.actions[0].type == "show_place"


def test_model_escalation(monkeypatch):
    service = scripted_service(monkeypatch, [("get_emergency_actions", {}), "Call now"])
    result = run(service.chat(ChatRequest(message="A concerning situation")))
    assert result.urgency == "urgent"


def test_model_failure_fallback(monkeypatch):
    service = scripted_service(monkeypatch, [])
    assert run(service.chat(ChatRequest(message="pharmacy"))).intent == "unavailable"


def test_session_expiry_and_unrecognized():
    from fastapi import HTTPException
    service = ChatService()
    token, conversation = service.conversation(None)
    assert service.conversation(token)[1] is conversation
    with pytest.raises(HTTPException):
        service.conversation("x" * 32)
    conversation.expires = time.monotonic() - 1
    with pytest.raises(HTTPException):
        service.conversation(token)


def test_location_freshness():
    now = datetime.now(timezone.utc)
    p = Position(lat=28, lon=77, accuracy_m=10, observed_at=now)
    assert not location_warnings(p)
    assert location_warnings(p.model_copy(update={"observed_at": now - timedelta(minutes=3)}))
    assert location_warnings(p.model_copy(update={"accuracy_m": 1000}))


def test_budget_and_coordinate_guard():
    context = SimpleNamespace(state={})
    assert agents.budget(SimpleNamespace(name="search"), {"x": 1}, context) is None
    assert agents.budget(SimpleNamespace(name="search"), {"x": 1}, context)["error"]
    with pytest.raises(ValueError):
        tools.confirmed_point(28, 77, context)
    context.state["turn:coordinate_evidence"] = "28, 77"
    assert tools.confirmed_point(28, 77, context) == (28, 77)


def test_maps_new_request_and_normalization():
    def handler(request):
        import json
        assert request.headers["x-goog-api-key"] == "fake-key"
        body = json.loads(request.content)
        assert body["includedTypes"] == ["pharmacy"]
        assert body["rankPreference"] == "DISTANCE"
        return httpx.Response(200, json={"places": [{"id": "abc", "displayName": {"text": "Fixture"},
            "location": {"latitude": 28.54, "longitude": 77.2}}]})
    result = run(MapsClient("fake-key", httpx.MockTransport(handler)).nearby("pharmacy", 28.54, 77.2))
    assert result["places"][0]["distance_m"] == 0
    assert result["places"][0]["open_now"] is None


def test_maps_error_does_not_leak_key():
    client = MapsClient("secret", httpx.MockTransport(lambda _: httpx.Response(403, text="secret")))
    with pytest.raises(MapsUnavailable) as error:
        run(client.nearby("police", 28, 77))
    assert str(error.value) == "maps_http_403"


def test_walking_request_is_not_driving():
    def handler(request):
        import json
        body = json.loads(request.content)
        assert body["travelMode"] == "WALK"
        assert "routingPreference" not in body
        return httpx.Response(200, json={"routes": [{"distanceMeters": 100, "duration": "90s"}]})
    result = run(MapsClient("fake", httpx.MockTransport(handler)).walking((28, 77), (28.1, 77.1)))
    assert result["scored"] is False


def test_unconfirmed_coordinates_do_not_call_maps(monkeypatch):
    async def forbidden(*args):
        pytest.fail("unconfirmed location reached Google")
    monkeypatch.setattr(MapsClient, "nearby", forbidden)
    result = run(tools.search_nearby_places("pharmacy", 28, 77, SimpleNamespace(state={})))
    assert "error" in result


def test_natural_during_trip_uses_attached_location(monkeypatch):
    calls = []

    async def nearby(self, kind, lat, lon):
        calls.append((kind, lat, lon))
        return {"places": [{"id": "fixture", "name": "Fixture police station", "kind": kind,
            "lat": lat, "lon": lon, "source": "test_fixture", "distance_m": 100,
            "distance_basis": "straight_line", "hours_basis": "unknown"}]}

    monkeypatch.setattr(MapsClient, "nearby", nearby)
    service = scripted_service(monkeypatch, [
        ("transfer_to_agent", {"agent_name": "during_trip"}),
        ("search_near_current_location", {"kind": "police"}), "Done"])
    result = run(service.chat(ChatRequest(
        message="I'm on my walk and need some assistance. Is there a police station nearby?",
        location=Position(lat=28.5433, lon=77.2067, accuracy_m=5,
                          observed_at=datetime.now(timezone.utc)))))
    assert calls == [("police", 28.5433, 77.2067)]
    assert result.intent == "during_trip"
    assert not result.needs_clarification
    assert result.places[0]["kind"] == "police"


@pytest.mark.parametrize("age,accuracy", [(180, 5), (0, 150)])
def test_current_location_tool_rejects_unusable_attachment(monkeypatch, age, accuracy):
    async def forbidden(*args):
        pytest.fail("Unusable attachment reached Maps")

    monkeypatch.setattr(MapsClient, "nearby", forbidden)
    service = scripted_service(monkeypatch, [
        ("transfer_to_agent", {"agent_name": "during_trip"}),
        ("search_near_current_location", {"kind": "police"}), "Done"])
    result = run(service.chat(ChatRequest(message="Is there a police station nearby?",
        location=Position(lat=28.5433, lon=77.2067, accuracy_m=accuracy,
            observed_at=datetime.now(timezone.utc) - timedelta(seconds=age)))))
    assert not result.places
    assert result.needs_clarification
    assert result.warnings


def test_current_location_tool_requires_attachment():
    context = SimpleNamespace(state={})
    result = run(tools.search_near_current_location("police", context))
    assert "error" in result
    assert context.state["turn:clarification"] == "location"


def test_selected_trip_comparison_no_coordinate_message(monkeypatch):
    service = scripted_service(monkeypatch, [
        ("transfer_to_agent", {"agent_name": "pre_trip"}),
        ("compare_selected_trip", {}), "Done"])
    result = run(service.chat(ChatRequest(
        message="For this walk, is the extra walking time worth it for better lighting and fewer weak stretches?",
        trip={"origin": {"lat": 28.5433, "lon": 77.2067},
              "destination": {"lat": 28.5286, "lon": 77.2195},
              "depart_at": "2026-10-16T21:30:00+05:30"})))
    assert result.intent == "pre_trip"
    assert len(result.routes) >= 2
    assert "extra minute" in result.reply
    assert "Lighting evidence" in result.reply
    assert "Mapped weak stretches" in result.reply
    assert "cannot establish which route is safe" in result.reply


def test_selected_trip_requires_timezone():
    client = TestClient(app)
    result = client.post("/api/chat", json={"message": "Compare this trip", "trip": {
        "origin": {"lat": 28.54, "lon": 77.2}, "destination": {"lat": 28.53, "lon": 77.21},
        "depart_at": "2026-10-16T21:30:00"}})
    assert result.status_code == 422


def test_compare_selected_trip_requires_attachment():
    context = SimpleNamespace(state={})
    assert "error" in run(tools.compare_selected_trip(context))


def test_comparison_missing_scores_is_not_a_recommendation():
    assert "enough scored alternatives" in tools.selected_trip_comparison([{"score": None}])
