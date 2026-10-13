"""Live check of every Google API SafeHer uses, with the keys in backend/.env.

    .venv/bin/python -m safeher.google_check

Makes one small real request per API (a few billable calls in total) and prints what works and,
for anything that doesn't, the exact error and what to enable. Never prints the keys.
"""

from __future__ import annotations

import math
import sys

import httpx

from . import config, google
from .areas import AREAS
from .explain import _env

ENABLE_HINT = {
    "Map Tiles API": "enable “Map Tiles API” on the key's Cloud project",
    "Places API (New)": "enable “Places API (New)” (not the legacy Places API)",
    "Routes API": "enable “Routes API”",
    "Street View Static API": "enable “Street View Static API”",
    "Gemini API": "create a key at https://aistudio.google.com/apikey and set GEMINI_API_KEY",
}


def tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def main() -> int:
    area = AREAS[config.app()["default_area"]]
    o, d = (next(l for l in area.landmarks if l.name == n) for n in area.default_trip)
    results: list[tuple[str, str, bool, str]] = []

    def check(api_name: str, what: str, fn):
        try:
            detail = fn()
            results.append((api_name, what, True, detail))
        except Exception as e:  # report every failure, keep going
            results.append((api_name, what, False, str(e)[:200]))

    if not google.api_key():
        print("GOOGLE_MAPS_API_KEY is not set in backend/.env — Maps checks skipped.")
    else:
        c = google.client()
        x, y = tile_xy(*area.center, 15)
        check("Map Tiles API", "light tile", lambda: f"{len(c.tile('light', 15, x, y)[0])} bytes")
        check("Map Tiles API", "dark tile", lambda: f"{len(c.tile('dark', 15, x, y)[0])} bytes")
        check("Map Tiles API", "attribution", lambda: c.viewport_copyright(
            "light", 15, area.north, area.south, area.east, area.west) or "(empty)")
        preds = []

        def ac():
            preds.extend(c.autocomplete("metro", (area.south, area.west, area.north, area.east)))
            return f"{len(preds)} suggestions, e.g. {preds[0]['main']!r}" if preds else "0 suggestions"
        check("Places API (New)", "autocomplete", ac)
        check("Places API (New)", "place details", lambda: (lambda p: f"{p['name']} at {p['lat']:.4f},{p['lon']:.4f}")(
            c.place(preds[0]["place_id"])) if preds else "skipped (no suggestion)")
        check("Places API (New)", "nearby + open now", lambda: (lambda ps: f"{len(ps)} police, open now: "
              f"{sum(p['open'] is True for p in ps)}")(c.nearby(o.lat, o.lon, ["police"], 2500, 5)))
        check("Routes API", "walking routes", lambda: (lambda rs: ", ".join(
            f"{r['distance_m']} m" for r in rs))(c.walking_routes((o.lat, o.lon), (d.lat, d.lon))))
        check("Street View Static API", "imagery at a landmark",
              lambda: "available" if c.streetview_available(o.lat, o.lon, 50) else "no imagery here")

    gemini_key = _env("GEMINI_API_KEY")
    if not gemini_key:
        print("GEMINI_API_KEY is not set in backend/.env — Gemini check skipped (template explanations are used).")
    else:
        model = _env("GEMINI_MODEL", "gemini-2.5-flash")

        def models():
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=15,
                          headers={"x-goog-api-key": gemini_key}, params={"pageSize": 200})
            r.raise_for_status()
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            flash = sorted(n for n in names if "flash" in n)
            if model not in names:
                raise RuntimeError(f"GEMINI_MODEL {model!r} not available to this key; flash models: {flash[-5:]}")
            return f"{model} available ({len(names)} models)"
        check("Gemini API", "model list", models)

        def generate():
            from .explain import gemini_generate
            text, used = gemini_generate("Reply with the single word: ready", "ping")
            return f"{used} replied {text[:40]!r}"
        check("Gemini API", "generate", generate)

    width = max((len(f"{a} · {w}") for a, w, _, _ in results), default=20)
    for api_name, what, ok, detail in results:
        print(f"{'✓' if ok else '✗'} {f'{api_name} · {what}':<{width}}  {detail}")
    failed = sorted({a for a, _, ok, _ in results if not ok})
    for a in failed:
        print(f"  → {a}: {ENABLE_HINT[a]}")
    if results and not failed:
        print("\nAll Google features are live. Restart the server to use them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
