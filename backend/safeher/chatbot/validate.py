"""Opt-in live smoke checks: python -m safeher.chatbot.validate [--chat].

Uses public demo coordinates. --chat may incur Maps charges and performs model inference.
Never prints keys, tokens, provider response bodies or place records.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

import httpx

from .maps import MapsClient, MapsUnavailable
from .models import ChatRequest
from .service import ChatService


async def check_ollama() -> bool:
    base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434").rstrip("/")
    model = os.getenv("CHATBOT_MODEL", "llama3.2:3b")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(base + "/api/show", json={"model": model})
            if response.status_code != 200:
                print(f"FAIL Ollama model unavailable. Download the configured CHATBOT_MODEL ({model}).")
                return False
            if "tools" not in response.json().get("capabilities", []):
                print("FAIL Ollama model does not advertise tool support.")
                return False
            response = await client.post(base + "/api/chat", json={
                "model": model, "stream": False, "options": {"temperature": 0},
                "messages": [{"role": "user", "content": "Call echo with text equal to validation."}],
                "tools": [{"type": "function", "function": {
                    "name": "echo", "description": "Echo the provided text.",
                    "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                                   "required": ["text"]}}}]})
            response.raise_for_status()
            calls = response.json().get("message", {}).get("tool_calls", [])
            ok = any(call.get("function", {}).get("name") == "echo" and
                     call["function"].get("arguments", {}).get("text") == "validation"
                     for call in calls)
            print(("PASS" if ok else "FAIL") + " Ollama structured tool-call probe.")
            return ok
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        print("FAIL Ollama transport or response; verify server and model configuration.")
        return False


async def check_maps() -> bool:
    try:
        result = await MapsClient().nearby("pharmacy", 28.5433, 77.2067)
        print(f"PASS Google Places live request ({len(result['places'])} results).")
        return True
    except MapsUnavailable as exc:
        print(f"FAIL Google Places: {exc}. Key validity is unverified on transport failure.")
        return False


async def check_sos() -> bool:
    start = time.monotonic()
    response = await ChatService().chat(ChatRequest(message="SOS", sos=True))
    ok = response.urgency == "urgent" and any(a.value == "tel:112" for a in response.actions)
    print(("PASS" if ok else "FAIL") + f" model-independent SOS ({time.monotonic() - start:.3f}s).")
    return ok


async def check_chat() -> bool:
    service = ChatService()
    missing = await service.chat(ChatRequest(message="Where is the nearest pharmacy?"))
    clarification = missing.needs_clarification and missing.intent == "local_enquiry"
    print(("PASS" if clarification else "FAIL") + " ADK supervisor delegation and missing-location clarification.")
    response = await service.chat(ChatRequest(
        message="Find the nearest pharmacy to these confirmed coordinates: 28.5433, 77.2067."))
    source_ok = bool(response.places) and all(p.get("source") == "google_places" for p in response.places)
    search_ok = response.intent == "local_enquiry" and source_ok
    print(("PASS" if search_ok else "FAIL") + " Ollama → ADK → Google Places journey (no local fallback counted).")
    return clarification and search_ok


async def validate(chat: bool = False) -> bool:
    sos = await check_sos()
    ollama = await check_ollama()
    maps = await check_maps()
    agent = False
    if chat and ollama and maps:
        agent = await check_chat()
    elif chat:
        print("SKIP ADK live journey: prerequisite checks failed.")
    else:
        print("SKIP ADK live journey: use --chat to opt in.")
    return sos and ollama and maps and (agent if chat else True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", action="store_true", help="Also test live ADK conversations")
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(validate(args.chat)) else 1)


if __name__ == "__main__":
    main()
