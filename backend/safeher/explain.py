"""Plain-language route explanations, grounded only in the computed plan (design spec §5.4).

Providers (SAFEHER_LLM=auto, the default, tries Gemini and then the template):
  - Gemini      — needs GEMINI_API_KEY in backend/.env (free key from Google AI Studio)
  - Ollama      — opt-in with SAFEHER_LLM=ollama; local model OLLAMA_MODEL (default phi3:mini)
  - Template    — deterministic sentences built from the same facts; always available

Every model answer goes through `check()` before it is shown. A failing answer gets one retry with
the problems listed; if it still fails, the next provider is used. The template is last because it
is faithful by construction, so the user always gets an explanation that only states facts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import config

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
LANGUAGES = {k: v["name"] for k, v in config.app()["languages"].items()}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"


def _env(name: str, default: str = "") -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE)
    except ImportError:
        pass
    return os.environ.get(name, default).strip()


# --- Facts: the only material a model may use ---------------------------------------------


def facts_from_plan(plan: dict) -> dict:
    """A compact, model-friendly view of a plan. Everything an explanation may mention is here."""
    routes = []
    for r in plan["routes"]:
        routes.append({
            "label": r["label"],
            "minutes": r["duration_min"],
            "distance_km": round(r["distance_m"] / 1000, 1),
            "score": r["score"],
            "band": r["band"],
            "signals": {b["signal"]: {"value": b["value"], "confidence": b["confidence"]} for b in r["breakdown"]},
            "reasons": r["reasons"],
            "weak_stretches": [{"length_m": w["length_m"], "score": w["score"], "main_factors": w["main_factors"]}
                               for w in r["weak_stretches"]],
            "stops": [{"type": s["label"], "name": s["name"], "km_along_route": round(s["along_route_m"] / 1000, 1),
                       "open": s["open"], "hours_assumed": s["hours_assumed"]} for s in r["stops"]],
        })
    return {
        "city": plan["area"]["city"],
        "departure_local_time": plan["departure_local"][11:16],
        "is_night": plan["night_fraction"] >= 0.5,
        "routes": routes,
        "data_notes": plan["data_notes"],
    }


# --- Guardrails -----------------------------------------------------------------------------

BANNED = {
    "en": [r"\bsafe\b", r"\bsafest\b", r"\bunsafe\b", r"\bdangerous\b", r"\bdanger\b", r"\bguarantee[sd]?\b",
           r"\bcrime[- ]free\b", r"\bno risk\b", r"\brisk[- ]free\b"],
    # "अधिक सुरक्षित" (safer) is allowed; bare "सुरक्षित" (safe), "असुरक्षित" (unsafe), "खतरनाक" (dangerous) are not.
    "hi": [r"(?<!अधिक )(?<!ज़्यादा )(?<!ज्यादा )सुरक्षित", r"असुरक्षित", r"खतरनाक", r"गारंटी"],
}
_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
DEVANAGARI = "[" + chr(0x0900) + "-" + chr(0x097F) + "]"
# Allowed: ASCII + Latin accents, Devanagari, general punctuation, rupee sign, typographic quotes/dashes.
UNEXPECTED_SCRIPT = ("[^" + chr(0) + "-" + chr(0x024F) + chr(0x0900) + "-" + chr(0x097F)
                     + chr(0x2000) + "-" + chr(0x206F) + chr(0x20B9) + "]")
MIXED_WORD = DEVANAGARI + "[A-Za-z]|[A-Za-z]" + DEVANAGARI
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _numbers_in(obj) -> set[float]:
    out: set[float] = set()
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.add(round(float(obj), 1))
    elif isinstance(obj, str):
        out |= {round(float(n), 1) for n in _NUM.findall(obj.translate(_DEVANAGARI_DIGITS).replace(",", ""))}
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _numbers_in(v)
    return out


def allowed_numbers(facts: dict) -> set[float]:
    nums = _numbers_in(facts)
    routes = facts["routes"]
    for a in routes:  # differences a model may reasonably state ("2 minutes longer", "10 points higher")
        for b in routes:
            nums.add(round(abs(a["minutes"] - b["minutes"]), 1))
            if a["score"] is not None and b["score"] is not None:
                nums.add(round(abs(a["score"] - b["score"]), 1))
    h, m = (int(x) for x in facts["departure_local_time"].split(":"))
    nums |= {float(h), float(m), float((h + 11) % 12 + 1)}  # 21:30 → 21, 30, 9
    nums |= {100.0, 112.0}  # "out of 100"; the emergency number
    for r in routes:  # total length of weak stretches ("2 weaker stretches, 615 m in total")
        nums.add(float(sum(w["length_m"] for w in r["weak_stretches"])))
        nums.add(float(len(r["weak_stretches"])))
    for r in routes:  # confidence shown as a percentage
        for s in r["signals"].values():
            nums.add(round(s["confidence"] * 100, 1))
    return nums


@dataclass
class CheckResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def check(text: str, facts: dict, lang: str, forbidden_names: set[str] = frozenset()) -> CheckResult:
    problems: list[str] = []
    if not text or not text.strip():
        return CheckResult(False, ["empty answer"])
    if len(text.strip()) < 20:
        problems.append("too short")  # keep checking, so a retry gets every problem at once
    if len(text) > 700:
        problems.append("too long (max ~3 sentences)")
    for pat in BANNED.get(lang, BANNED["en"]) + (BANNED["en"] if lang != "en" else []):
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            problems.append(f"uses forbidden wording: '{m.group(0)}'")
    allowed = allowed_numbers(facts)
    for n in _numbers_in(text):
        if n not in allowed:
            problems.append(f"number not in the data: {n:g}")
    known = {r["label"].lower() for r in facts["routes"]} | {
        (s["name"] or "").lower() for r in facts["routes"] for s in r["stops"]}
    lowered = text.lower()
    for name in forbidden_names:
        if len(name) > 4 and name.lower() in lowered and name.lower() not in known:
            problems.append(f"mentions a place not in the route data: '{name}'")
    if lang == "hi" and not re.search(DEVANAGARI, text):
        problems.append("not written in Hindi")
    # Small models drift into other scripts or glue scripts together mid-word ("रoute").
    if re.search(UNEXPECTED_SCRIPT, text):
        problems.append("contains characters from an unexpected script")
    if re.search(MIXED_WORD, text):
        problems.append("mixes scripts inside a word")
    # Must state the scores it compares, so a reader can match the text to the route cards.
    best = facts["routes"][0]
    shortest = next((r for r in facts["routes"] if r["label"] == "Shortest"), best)
    stated = _numbers_in(text)
    for r in ([best] if best is shortest else [best, shortest]):
        if r["score"] is not None and float(r["score"]) not in stated:
            problems.append(f"doesn't state the {r['label']} route's score ({r['score']})")
    return CheckResult(not problems, problems)


# --- Prompt ---------------------------------------------------------------------------------

SYSTEM = """You explain walking-route comparisons for SafeHer, a women's mobility app.
Write {n_sentences} short sentences in {language} for someone deciding which route to walk.
Rules:
- Use ONLY the facts in the JSON. Do not add places, numbers, opinions or advice that are not there.
- Compare the first route (highest score) with the "Shortest" route: time difference, score difference, and the 1–2 signals that explain it (lighting, open places nearby, transit nearby, weak stretches).
- If useful, mention ONE named stop from the facts (e.g. a police station or washroom along the route).
- If lighting confidence is low, say lighting data is limited rather than calling a street lit or dark.
- Never say a route is "safe", "unsafe", "safest" or "dangerous", and never guarantee anything. Say "more support", "better lit", "more places open" instead.
- Plain text only: no markdown, no lists, no greetings."""


def build_prompt(facts: dict, lang: str) -> tuple[str, str]:
    system = SYSTEM.format(n_sentences="2–3", language=LANGUAGES.get(lang, "English"))
    user = "Facts (JSON):\n" + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    return system, user


# --- Providers ------------------------------------------------------------------------------


class ProviderUnavailable(Exception):
    pass


def gemini_generate(system: str, user: str, timeout: float = 20.0) -> tuple[str, str]:
    key = _env("GEMINI_API_KEY")
    if not key:
        raise ProviderUnavailable("GEMINI_API_KEY not set")
    model = _env("GEMINI_MODEL", "gemini-2.5-flash")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
    }
    try:
        r = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=timeout,
                       headers={"x-goog-api-key": key})
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"Gemini unreachable: {e}") from e
    if r.status_code != 200:
        raise ProviderUnavailable(f"Gemini HTTP {r.status_code}: {r.text[:200]}")
    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip(), model


def ollama_generate(system: str, user: str, timeout: float = 60.0) -> tuple[str, str]:
    model = _env("OLLAMA_MODEL", "phi3:mini")
    try:
        r = httpx.post(_env("OLLAMA_URL", DEFAULT_OLLAMA_URL), timeout=timeout, json={
            "model": model, "system": system, "prompt": user, "stream": False,
            "options": {"temperature": 0.2, "num_predict": 220},
        })
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"Ollama unreachable: {e}") from e
    if r.status_code != 200:
        raise ProviderUnavailable(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
    return r.json().get("response", "").strip(), model


# --- Template (faithful by construction) ----------------------------------------------------

_SIGNAL_PHRASES = {
    "en": {"lighting": "better lighting data", "safe_spaces": "more places open nearby",
           "transit": "closer bus and metro stops", "reports": "fewer recent reports"},
    "hi": {"lighting": "बेहतर रोशनी के आंकड़े", "safe_spaces": "आसपास ज़्यादा खुली जगहें",
           "transit": "पास में बस और मेट्रो स्टॉप", "reports": "कम हाल की शिकायतें"},
}


def template_explanation(facts: dict, lang: str = "en") -> str:
    routes = facts["routes"]
    best = routes[0]
    shortest = next((r for r in routes if r["label"] == "Shortest"), best)
    phr = _SIGNAL_PHRASES.get(lang, _SIGNAL_PHRASES["en"])
    # Which signals most favour the best route over the shortest?
    gains = []
    for k in ("safe_spaces", "lighting", "transit", "reports"):
        a, b = best["signals"][k]["value"], shortest["signals"][k]["value"]
        if a is not None and b is not None and a - b >= 3:
            gains.append((a - b, k))
    gains.sort(reverse=True)
    why = [phr[k] for _, k in gains[:2]]
    stop = next((s for s in best["stops"] if s["name"] and s["type"] in ("police station", "washroom")), None)
    extra = best["minutes"] - shortest["minutes"]

    if lang == "hi":
        if best is shortest:
            s1 = f"सबसे छोटा रास्ता ({best['minutes']} मिनट) का स्कोर भी सबसे अधिक है: {best['score']}।"
        else:
            t = f"{extra} मिनट ज़्यादा लेता है" if extra > 0 else "लगभग उतना ही समय लेता है"
            s1 = (f"'{best['label']}' रास्ता {t} और इसका स्कोर {best['score']} है, "
                  f"जबकि सबसे छोटे रास्ते का स्कोर {shortest['score']} है।")
        s2 = f" इसका कारण है {' और '.join(why)}।" if why else ""
        weak = len(shortest["weak_stretches"])
        s3 = f" सबसे छोटे रास्ते पर {weak} कमज़ोर हिस्से हैं।" if weak and best is not shortest else ""
        s4 = f" रास्ते में {stop['name']} ({stop['type']}) पड़ता है।" if stop and not s3 else ""
        return (s1 + s2 + s3 + s4).strip()

    if best is shortest:
        s1 = f"The shortest route ({best['minutes']} min) also has the highest score, {best['score']}."
    else:
        t = f"takes {extra} min longer" if extra > 0 else "takes about the same time"
        s1 = f"The {best['label']} route {t} but scores {best['score']} vs {shortest['score']} for the shortest route."
    s2 = f" It has {' and '.join(why)}." if why else ""
    weak = shortest["weak_stretches"]
    s3 = ""
    if weak and best is not shortest:
        total = sum(w["length_m"] for w in weak)
        s3 = f" The shortest route has {len(weak)} weaker stretch{'es' if len(weak) > 1 else ''} ({total} m in total)."
    elif stop:
        s3 = f" It passes {stop['name']} ({stop['type']}) {stop['km_along_route']} km along the way."
    lighting = best["signals"]["lighting"]
    s4 = " Lighting data is limited on this route." if lighting["confidence"] < 0.35 and len(s1 + s2 + s3) < 260 else ""
    return (s1 + s2 + s3 + s4).strip()


# --- Pipeline -------------------------------------------------------------------------------

PROVIDERS = {"gemini": gemini_generate, "ollama": ollama_generate}
_CACHE: dict[str, dict] = {}
_CACHE_MAX = 128


def provider_order() -> list[str]:
    choice = _env("SAFEHER_LLM", "auto").lower()
    if choice == "template":
        return []
    if choice in PROVIDERS:
        return [choice]
    # Auto: Gemini when a key is configured, else the template. The local model is opt-in
    # (SAFEHER_LLM=ollama): phi3:mini took 6-12 s and wrote unusable Hindi in testing.
    return ["gemini"]


def explain(plan: dict, lang: str = "en", forbidden_names: set[str] = frozenset(),
            providers: dict | None = None, order: list[str] | None = None) -> dict:
    """Return {text, provider, model, lang, checked, attempts}."""
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; choose from {', '.join(LANGUAGES)}")
    facts = facts_from_plan(plan)
    providers = providers or PROVIDERS
    order = provider_order() if order is None else order
    key = hashlib.sha256(json.dumps([facts, lang, order], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if key in _CACHE and providers is PROVIDERS:
        return _CACHE[key]

    system, user = build_prompt(facts, lang)
    attempts: list[dict] = []
    result = None
    for name in order:
        prompt = user
        for attempt in range(2):
            try:
                text, model = providers[name](system, prompt)
            except ProviderUnavailable as e:
                attempts.append({"provider": name, "error": str(e)})
                break
            verdict = check(text, facts, lang, forbidden_names)
            attempts.append({"provider": name, "ok": verdict.ok, "problems": verdict.problems})
            if verdict.ok:
                result = {"text": text, "provider": name, "model": model}
                break
            prompt = (user + "\n\nYour previous answer was rejected for: " + "; ".join(verdict.problems)
                      + ". Rewrite it following every rule.")
        if result:
            break
    if result is None:
        text = template_explanation(facts, lang)
        verdict = check(text, facts, lang, forbidden_names)
        attempts.append({"provider": "template", "ok": verdict.ok, "problems": verdict.problems})
        result = {"text": text, "provider": "template", "model": None}
    result.update({"lang": lang, "checked": True, "attempts": attempts})
    if providers is PROVIDERS:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = result
    return result
