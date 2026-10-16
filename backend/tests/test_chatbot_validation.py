"""Live validation must not count a fallback as a successful Google journey."""
import asyncio

import pytest

from safeher.chatbot import validate
from safeher.chatbot.models import ChatResponse


def test_failed_prerequisites_skip_live_agent(monkeypatch):
    async def passed():
        return True

    async def failed():
        return False

    async def forbidden():
        pytest.fail("Agent should not run when a prerequisite failed")

    monkeypatch.setattr(validate, "check_sos", passed)
    monkeypatch.setattr(validate, "check_ollama", failed)
    monkeypatch.setattr(validate, "check_maps", passed)
    monkeypatch.setattr(validate, "check_chat", forbidden)
    assert asyncio.run(validate.validate(chat=True)) is False


@pytest.mark.parametrize("source,expected", [("openstreetmap", False), ("google_places", True)])
def test_live_journey_requires_google_results(monkeypatch, source, expected):
    class FakeService:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(reply="Which location?", intent="local_enquiry", needs_clarification=True)
            return ChatResponse(reply="Results", intent="local_enquiry", places=[{"source": source}])

    monkeypatch.setattr(validate, "ChatService", FakeService)
    assert asyncio.run(validate.check_chat()) is expected


def test_sos_validation_works_without_external_services():
    assert asyncio.run(validate.check_sos()) is True
