"""Grounding with Google Maps (Gemini on Vertex AI): answers about places, with Google Maps sources.

Used by HerSide's ask_google_maps tool for questions SafeHer's own data can't answer (what a place is
like, whether it's open late, what reviews say). Needs GOOGLE_CLOUD_PROJECT and Application Default
Credentials; billed per Google Maps query. Google's terms: every answer is shown with its sources right
after it, each attributed to "Google Maps" with the returned title and a link to the returned URI.
"""
from __future__ import annotations

import asyncio

import httpx

from .. import config
from ..explain import BANNED, ProviderUnavailable, _env, gemini_model, thinking, vertex_headers

CFG = config.app()["maps_grounding"]
URL = ("https://aiplatform.googleapis.com/v1beta1/projects/{project}/locations/global/publishers/google/"
       "models/{model}:generateContent")
SYSTEM = ("You answer questions about places for a women's walking-safety app in India, and only their "
          "safety side: whether a place is open late or staffed, how busy or deserted it gets at night, "
          "remarks in reviews about lighting, crowds, security or getting there, and nearby police, "
          "hospitals or transit. Ignore food, drink, prices, decor and entertainment; if the question is "
          "only about those, say you can only cover safety-related facts. Use Google Maps information "
          "only and say when it doesn't cover something. 2-4 short sentences. Describe facts; never call a "
          "place or area safe, unsafe or dangerous, and never guarantee anything. If someone is in "
          "immediate trouble, tell them to call 112.")


class NotConfigured(ProviderUnavailable):
    """No Cloud project: the tool is off, which is not an error."""


def parse(data: dict) -> dict:
    """Text and Google Maps sources from a generateContent response."""
    candidate = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", [])).strip()
    text = text.replace("**", "")  # the chat shows plain text, not Markdown
    sources, seen = [], set()
    for chunk in candidate.get("groundingMetadata", {}).get("groundingChunks", []):
        m = chunk.get("maps")
        if not m or not m.get("uri") or m["uri"] in seen:
            continue
        seen.add(m["uri"])
        reviews = [{"title": r.get("title"), "uri": r.get("uri")}
                   for r in (m.get("placeAnswerSources") or {}).get("reviewSnippets", []) if r.get("uri")]
        sources.append({"title": m.get("title") or "Place", "uri": m["uri"], "place_id": m.get("placeId"),
                        "reviews": reviews[:3]})
    return {"text": text, "sources": sources[: int(CFG["max_sources"])]}


def wording_problems(text: str) -> list[str]:
    """The same forbidden wording as route explanations: no 'safe', 'unsafe', 'dangerous', guarantees."""
    import re
    return [m.group(0) for pat in BANNED["en"] if (m := re.search(pat, text, flags=re.IGNORECASE))]


async def ask(question: str, near: tuple[float, float] | None, transport=None) -> dict:
    """A grounded answer whose wording passes the checks; one retry, then an error (never unchecked)."""
    out = await _ask(question, near, transport, SYSTEM)
    bad = wording_problems(out["text"])
    if bad:
        out = await _ask(question, near, transport, SYSTEM + " Your previous answer used the words "
                         + ", ".join(repr(b) for b in bad) + "; describe facts without them.")
        bad = wording_problems(out["text"])
        if bad:
            raise ProviderUnavailable("Google Maps answer used wording SafeHer doesn't allow: " + ", ".join(bad))
    return out


async def _ask(question: str, near: tuple[float, float] | None, transport, system: str) -> dict:
    project = _env("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise NotConfigured("Google Maps grounding needs GOOGLE_CLOUD_PROJECT (Vertex AI).")
    headers = await asyncio.to_thread(vertex_headers, project)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": question}]}],
        "tools": [{"googleMaps": {}}],
        # Thinking off: unlimited, the model spent the allowance thinking and cut the answer off.
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": int(CFG["max_output_tokens"]),
                             "thinkingConfig": thinking(CFG["thinking_budget"])},
    }
    retrieval = {"languageCode": CFG["language"]}
    if near:
        retrieval["latLng"] = {"latitude": near[0], "longitude": near[1]}
    body["toolConfig"] = {"retrievalConfig": retrieval}
    url = URL.format(project=project, model=gemini_model(_env("MAPS_GROUNDING_MODEL", CFG["model"])))
    try:
        async with httpx.AsyncClient(timeout=float(CFG["timeout_s"]), transport=transport) as client:
            r = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"Google Maps grounding unreachable: {type(e).__name__}") from e
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise ProviderUnavailable(f"Google Maps grounding failed: HTTP {r.status_code} {' '.join(msg.split())[:160]}")
    out = parse(r.json())
    if not out["text"]:
        raise ProviderUnavailable("Google Maps grounding returned no answer.")
    if (r.json().get("candidates") or [{}])[0].get("finishReason") == "MAX_TOKENS":
        raise ProviderUnavailable("Google Maps grounding answer was cut off.")
    return out
