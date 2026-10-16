"""Bounded chat turns. Sessions are ephemeral, bearer-protected and isolated."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agents import build_agent_team
from .models import (ChatRequest, ChatResponse, emergency_response, is_urgent,
                     location_warnings)
from .tools import QUESTIONS, render_evidence

APP_NAME = "safeher_companion"
SESSION_TTL = 30 * 60
MAX_SESSIONS = 200
router = APIRouter(prefix="/api/chat", tags=["Companion"])


@dataclass
class Conversation:
    expires: float
    messages: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ChatService:
    def __init__(self, runner_factory=None):
        self.conversations: dict[str, Conversation] = {}
        self.runner_factory = runner_factory

    def conversation(self, token):
        now = time.monotonic()
        self.conversations = {key: value for key, value in self.conversations.items()
                              if value.expires > now or value.lock.locked()}
        if token is not None:
            key = hashlib.sha256(token.encode()).hexdigest()
            conversation = self.conversations.get(key)
            if conversation is None or conversation.expires <= now:
                raise HTTPException(404, "Chat session expired or unavailable.")
            return token, conversation
        if len(self.conversations) >= MAX_SESSIONS:
            raise HTTPException(503, "Chat capacity reached; try again later.")
        token = secrets.token_urlsafe(32)
        conversation = Conversation(now + SESSION_TTL)
        self.conversations[hashlib.sha256(token.encode()).hexdigest()] = conversation
        return token, conversation

    async def chat(self, req: ChatRequest) -> ChatResponse:
        # Must not initialize ADK, wait on a session lock, or call an external service.
        if is_urgent(req):
            return emergency_response(req)
        token, conversation = self.conversation(req.session_id)
        if conversation.lock.locked():
            raise HTTPException(409, "A chat turn is already running for this session.")
        async with conversation.lock:
            response = await self.run_turn(req, conversation)
            conversation.messages = (conversation.messages + [req.message, response.reply])[-6:]
            conversation.expires = time.monotonic() + SESSION_TTL
            response.session_id = token
            return response

    async def run_turn(self, req, conversation):
        sessions = InMemorySessionService()
        user = secrets.token_hex(16)
        session = await sessions.create_session(app_name=APP_NAME, user_id=user)
        observed = {}
        try:
            runner = (self.runner_factory(sessions) if self.runner_factory else
                      Runner(app_name=APP_NAME, agent=build_agent_team(), session_service=sessions))
            warnings = location_warnings(req.location)
            observed = {"turn:coordinate_evidence": " ".join(conversation.messages + [req.message])}
            if req.location and not warnings:
                observed["turn:location"] = {"lat": req.location.lat, "lon": req.location.lon}
            # Do not reuse stale GPS or expose coordinates to the model when unusable.
            context = {"language": req.lang, "recent_dialogue": conversation.messages,
                       "current_message": req.message, "location_warnings": warnings,
                       "current_time": datetime.now(timezone.utc).isoformat()}
            if req.location and not warnings:
                context["confirmed_current_location"] = req.location.model_dump(mode="json")
            content = types.Content(role="user", parts=[types.Part(text=json.dumps(context, ensure_ascii=False))])
            async with asyncio.timeout(60):
                async for event in runner.run_async(user_id=user, session_id=session.id,
                    new_message=content, state_delta=observed,
                    run_config=RunConfig(max_llm_calls=6)):
                    observed.update(event.actions.state_delta or {})
                    if observed.get("turn:emergency"):
                        return emergency_response(req)
            if observed.get("turn:emergency"):
                return emergency_response(req)
            evidence = observed.get("turn:evidence", [])
            response = render_evidence(evidence)
            response.intent = observed.get("turn:intent", response.intent)
            if not evidence:
                topic = observed.get("turn:clarification", "scope")
                response.reply = QUESTIONS.get(topic, QUESTIONS["scope"])
                response.needs_clarification = True
            if observed.get("turn:budget_exceeded"):
                response.warnings.append("Agent tool-call limit reached; please narrow the request.")
            response.warnings.extend(warnings)
            return response
        except Exception:
            # Do not log exception text: HTTP/model errors may embed sensitive context or keys.
            return ChatResponse(reply="The assistant is unavailable right now. Try again or use the nearby-place search. "
                                "For an emergency in India, call 112.", intent="unavailable",
                                warnings=["Model/tool execution failed or exceeded its time limit."])
        finally:
            await sessions.delete_session(app_name=APP_NAME, user_id=user, session_id=session.id)


service = ChatService()


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    return await service.chat(req)
