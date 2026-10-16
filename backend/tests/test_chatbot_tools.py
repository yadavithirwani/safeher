"""Tool-level validation, local fallback and bounded session checks."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from safeher.chatbot import agents, tools
from safeher.chatbot.maps import MapsClient, MapsUnavailable
from safeher.chatbot.models import ChatRequest
from safeher.chatbot.service import ChatService
from tests.test_chatbot import scripted_service


def test_google_failure_uses_labelled_local_fallback(monkeypatch):
    async def unavailable(*args):
        raise MapsUnavailable("maps_unavailable")
    monkeypatch.setattr(MapsClient, "nearby", unavailable)
    monkeypatch.setattr(tools, "local_places", lambda *args: [])
    context = SimpleNamespace(state={"turn:coordinate_evidence": "28, 77"})
    result = asyncio.run(tools.search_nearby_places("pharmacy", 28, 77, context))
    assert result["source"] == "openstreetmap"
    assert "cached" in result["warning"]


def test_no_place_details_without_search():
    result = asyncio.run(tools.get_place_details("unknown", SimpleNamespace(state={})))
    assert "error" in result


def test_plan_requires_confirmed_departure():
    context = SimpleNamespace(state={"turn:coordinate_evidence": "28.54,77.2 and 28.53,77.21"})
    result = asyncio.run(tools.plan_supported_trip(28.54, 77.2, 28.53, 77.21,
                        "2026-10-16T23:00:00+05:30", context))
    assert "explicitly confirmed" in result["error"]


def test_plan_b_requires_current_turn_route():
    assert "error" in tools.get_plan_b_coverage("Shortest", SimpleNamespace(state={}))


def test_whole_turn_starts_with_supervisor(monkeypatch):
    service = scripted_service(monkeypatch, [
        ("transfer_to_agent", {"agent_name": "local_enquiry"}),
        ("ask_clarification", {"topic": "location"}), "done",
        ("transfer_to_agent", {"agent_name": "pre_trip"}),
        ("ask_clarification", {"topic": "destination"}), "done"])
    async def turns():
        first = await service.chat(ChatRequest(message="nearest pharmacy?"))
        second = await service.chat(ChatRequest(message="Now plan a trip home", session_id=first.session_id))
        return first, second
    first, second = asyncio.run(turns())
    assert first.intent == "local_enquiry"
    assert second.intent == "pre_trip"
    assert "home" in second.reply
    assert first.session_id == second.session_id


def test_hindi_emergency():
    result = asyncio.run(ChatService().chat(ChatRequest(message="SOS", lang="hi")))
    assert "112" in result.reply and "कॉल" in result.reply


def test_budget_counts_distinct_calls():
    context = SimpleNamespace(state={})
    for i in range(8):
        assert agents.budget(SimpleNamespace(name="test"), {"x": i}, context) is None
    assert "error" in agents.budget(SimpleNamespace(name="test"), {"x": 9}, context)


def test_locked_session_is_rejected_without_waiting():
    async def scenario():
        service = ChatService()
        token, conversation = service.conversation(None)
        async with conversation.lock:
            with pytest.raises(HTTPException) as error:
                await service.chat(ChatRequest(message="hello", session_id=token))
            assert error.value.status_code == 409
    asyncio.run(scenario())


@pytest.mark.parametrize('value', [None, True, 'invalid', float('nan'), float('inf')])
def test_bad_model_coordinates_are_rejected(value):
    context = SimpleNamespace(state={})
    assert agents.budget(SimpleNamespace(name='search_nearby_places'),
                         {'lat': value, 'lon': 77}, context)['error']
    assert context.state['turn:clarification'] == 'location'


def test_numeric_model_coordinates_are_normalized():
    context = SimpleNamespace(state={})
    args = {'lat': '28.5433', 'lon': '77.2067'}
    assert agents.budget(SimpleNamespace(name='search_nearby_places'), args, context) is None
    assert args == {'lat': 28.5433, 'lon': 77.2067}


def test_specialist_without_tool_output_gets_relevant_clarification(monkeypatch):
    service = scripted_service(monkeypatch, [
        ('transfer_to_agent', {'agent_name': 'local_enquiry'}), 'Please provide your location.'])
    result = asyncio.run(service.chat(ChatRequest(message='nearest pharmacy?')))
    assert result.needs_clarification
    assert 'which location' in result.reply
