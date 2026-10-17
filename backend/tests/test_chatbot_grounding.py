"""Grounding with Google Maps for HerSide: request shape, parsing, failures and rendering (mocked)."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from safeher.chatbot import grounding, tools
from safeher.explain import ProviderUnavailable

REPLY = {"candidates": [{"finishReason": "STOP",
    "content": {"parts": [{"text": "**Cafe Tesu** is open until midnight."}]},
    "groundingMetadata": {"groundingChunks": [
        {"maps": {"uri": "https://maps.google.com/maps?cid=1", "title": "Cafe Tesu - Google Maps", "placeId": "ChIJ1",
                  "placeAnswerSources": {"reviewSnippets": [{"id": "r1", "title": "Review", "uri": "https://maps.google.com/r1"}]}}},
        {"maps": {"uri": "https://maps.google.com/maps?cid=1", "title": "duplicate"}},
        {"web": {"uri": "https://example.com"}}]}}]}


def transport(seen, status=200, body=REPLY):
    def handle(request):
        seen.append(request)
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handle)


@pytest.fixture
def vertex(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "safeher-demo")
    monkeypatch.setattr(grounding, "vertex_headers", lambda project: {"Authorization": "Bearer tok"})


def test_request_uses_the_maps_tool_location_and_project(vertex):
    seen = []
    out = asyncio.run(grounding.ask("cafes open late?", (28.54, 77.21), transport(seen)))
    req = seen[0]
    body = json.loads(req.content)
    assert "/projects/safeher-demo/locations/global/" in str(req.url) and req.headers["Authorization"] == "Bearer tok"
    assert body["tools"] == [{"googleMaps": {}}]
    assert body["toolConfig"]["retrievalConfig"]["latLng"] == {"latitude": 28.54, "longitude": 77.21}
    assert out["text"] == "Cafe Tesu is open until midnight."  # Markdown stripped for the plain-text chat
    assert out["sources"] == [{"title": "Cafe Tesu - Google Maps", "uri": "https://maps.google.com/maps?cid=1",
                               "place_id": "ChIJ1", "reviews": [{"title": "Review", "uri": "https://maps.google.com/r1"}]}]


def test_without_a_cloud_project_it_is_off_and_errors_are_errors(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
    with pytest.raises(grounding.NotConfigured):
        asyncio.run(grounding.ask("q?", None))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setattr(grounding, "vertex_headers", lambda project: {"Authorization": "Bearer tok"})
    with pytest.raises(ProviderUnavailable, match="HTTP 403"):
        asyncio.run(grounding.ask("q?", None, transport([], 403, {"error": {"message": "denied"}})))
    cut = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "Half an ans"}]}}]}
    with pytest.raises(ProviderUnavailable, match="cut off"):
        asyncio.run(grounding.ask("q?", None, transport([], 200, cut)))


def test_tool_biases_to_the_attached_location_then_the_trip(vertex, monkeypatch):
    calls = []
    async def fake(question, near, transport=None):
        calls.append(near)
        return {"text": "Answer", "sources": [{"title": "T", "uri": "https://maps.google.com/x"}]}
    monkeypatch.setattr(grounding, "ask", fake)
    ctx = SimpleNamespace(state={"turn:location": {"lat": 12.97, "lon": 77.64},
                                 "turn:trip": {"destination": {"lat": 28.5, "lon": 77.2}}})
    asyncio.run(tools.ask_google_maps("Is Toit busy late?", ctx))
    ctx2 = SimpleNamespace(state={"turn:trip": {"destination": {"lat": 28.5, "lon": 77.2}}})
    asyncio.run(tools.ask_google_maps("Is it busy late?", ctx2))
    assert calls == [(12.97, 77.64), (28.5, 77.2)]
    response = tools.render_evidence(ctx.state["turn:evidence"])
    assert response.reply == "Answer" and response.sources[0]["uri"] == "https://maps.google.com/x"
    assert any("Google Maps information" in w for w in response.warnings)


def test_tool_failure_is_shown_not_hidden(vertex, monkeypatch):
    async def down(question, near, transport=None):
        raise ProviderUnavailable("Google Maps grounding failed: HTTP 429 quota")
    monkeypatch.setattr(grounding, "ask", down)
    ctx = SimpleNamespace(state={})
    asyncio.run(tools.ask_google_maps("cafes open late?", ctx))
    assert "HTTP 429" in tools.render_evidence(ctx.state["turn:evidence"]).reply



def test_grounding_with_a_shared_vertex_key(monkeypatch):
    import asyncio as _a
    monkeypatch.setenv("VERTEX_API_KEY", "vk")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
    seen = []
    _a.run(grounding.ask("cafes open late?", (28.54, 77.21), transport(seen)))
    req = seen[0]
    assert str(req.url) == ("https://aiplatform.googleapis.com/v1beta1/publishers/google/models/"
                            "gemini-2.5-flash:generateContent")
    assert req.headers["x-goog-api-key"] == "vk" and "authorization" not in req.headers
