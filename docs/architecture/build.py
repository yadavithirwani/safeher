"""Generate docs/architecture/: the two diagrams as standalone SVGs (rendered on GitHub) and the full
page as HTML. Run from anywhere: python3 docs/architecture/build.py
"""
from html import escape as E
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------- Figure 1: data sources and how each is handled ----------
def box(x, y, w, h, cls="box", dashed=False):
    d = ' stroke-dasharray="6 5"' if dashed else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="{cls}"{d}/>'

def text(x, y, s, cls="t", anchor="start"):
    return f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{E(s)}</text>'

def arrow(points, label=None, lx=None, ly=None, cls="edge", anchor="middle", lcls="lbl"):
    pts = " ".join(f"{x},{y}" for x, y in points)
    out = f'<polyline points="{pts}" class="{cls}" marker-end="url(#ah)"/>'
    if label:
        out += text(lx, ly, label, lcls, anchor)
    return out

f1 = []
W1, H1 = 1140, 660
SX, SW = 20, 300           # sources
BX, BW = 372, 220          # build pipeline
DX, DW = 632, 210          # stores
KX, KW = 942, 178          # backend
sources = [
    ("Geofabrik OSM extracts", "streets, lit tags, places, lamps, districts", "build · no key", False),
    ("NASA Black Marble (LAADS)", "night lights, ~500 m pixels", "build · Earthdata token", False),
    ("NCRB via India Data Portal", "Delhi crimes against women, by district", "build · no key", False),
    ("Bengaluru City Police · OpenCity", "city crime trend 2021–2023", "build · no key · planned", True),
    ("Google Maps Platform", "tiles, places, routes, Street View", "live · API key", False),
    ("Gemini API", "route explanations", "live · API key", False),
    ("People using SafeHer", "reports, live GPS while sharing", "live · in the app", False),
]
y0, sh, gap = 64, 62, 14
mids = []
for i, (name, what, how, dashed) in enumerate(sources):
    y = y0 + i * (sh + gap)
    live = how.startswith("live")
    f1.append(box(SX, y, SW, sh, "box src-live" if live else "box src", dashed))
    f1.append(text(SX + 14, y + 22, name, "t strong"))
    f1.append(text(SX + 14, y + 40, what, "t small"))
    f1.append(text(SX + 14, y + 55, how, "t tag-live" if live else "t tag"))
    mids.append(y + sh / 2)

f1.append(text(SX, 44, "SOURCES", "t eyebrow"))
f1.append(text(BX, 44, "FETCHED ONCE", "t eyebrow"))
f1.append(text(DX, 44, "STORED", "t eyebrow"))
f1.append(text(KX, 44, "SERVED", "t eyebrow"))

# Build pipeline spans the four build sources
by, bh = y0, 4 * (sh + gap) - gap
f1.append(box(BX, by, BW, bh, "box build"))
for j, line in enumerate(["Build pipeline", "python -m safeher.data build", "download + verify checksum",
                          "cut each city’s bbox", "walk rules, place types", "validate, then swap in"]):
    f1.append(text(BX + 14, by + 26 + j * 22, line, "t strong" if j == 0 else ("t mono" if j == 1 else "t small")))
for m in mids[:4]:
    f1.append(arrow([(SX + SW, m), (BX - 4, m)]))

# Reference DuckDB
f1.append(box(DX, by, DW, bh, "box store", dashed=True))
for j, line in enumerate(["safeher.duckdb", "read-only, shared by workers", "segments · 1.6 M streets",
                          "places · 84 k", "night_lights · 21 k pixels", "districts → incidents", "city_stats (planned)"]):
    f1.append(text(DX + 14, by + 26 + j * 22, line, "t strong" if j == 0 else "t small"))
f1.append(arrow([(BX + BW, by + bh / 2), (DX - 4, by + bh / 2)], "writes", (BX + BW + DX) / 2, by + bh / 2 - 8))

# Backend
ky, kh = y0, 7 * (sh + gap) - gap
f1.append(box(KX, ky, KW, kh, "box backend"))
for j, line in enumerate(["Backend", "FastAPI · 4 workers", "", "corridor builder", "(in-memory cache)",
                          "routing · scoring", "Plan B coverage", "explain + fact checks", "search · nearby",
                          "reports · sharing", "", "No key ever", "reaches the browser"]):
    if line:
        f1.append(text(KX + 14, ky + 26 + j * 22, line, "t strong" if j == 0 else "t small"))
f1.append(arrow([(DX + DW, by + bh / 2), (KX - 4, by + bh / 2)], "reads corridor", (DX + DW + KX) / 2, by + bh / 2 - 8))

# Live sources straight to the backend
f1.append(arrow([(SX + SW, mids[4]), (KX - 4, mids[4])], "per request · nothing stored", (BX + DX + DW) / 2, mids[4] - 8))
f1.append(arrow([(SX + SW, mids[5]), (KX - 4, mids[5])], "per request · fact-checked", (BX + DX + DW) / 2, mids[5] - 8))

# User data store
uy, uh = y0 + 6 * (sh + gap) - 6, 74
f1.append(box(DX, uy, DW, uh, "box store", dashed=True))
f1.append(text(DX + 14, uy + 24, "safeher.sqlite3", "t strong"))
f1.append(text(DX + 14, uy + 44, "reports · live-sharing sessions", "t small"))
f1.append(text(DX + 14, uy + 62, "hashed links, deleted after 2 h", "t small"))
f1.append(f'<line x1="{KX}" y1="{uy + uh / 2}" x2="{DX + DW + 4}" y2="{uy + uh / 2}" class="edge" marker-end="url(#ah)"/>')
f1.append(text((DX + DW + KX) / 2, uy + uh / 2 - 8, "writes", "lbl", "middle"))
f1.append(arrow([(SX + SW, mids[6]), (BX + 60, mids[6]), (BX + 60, H1 - 20), (KX + KW / 2, H1 - 20), (KX + KW / 2, ky + kh + 4)],
                "app and /live viewer, over HTTPS", BX + 120, H1 - 28, anchor="start"))

fig1 = (f'<svg viewBox="0 0 {W1} {H1}" role="img" aria-label="Seven data sources. Four are fetched once by a build '
        'pipeline into a read-only DuckDB database; Google Maps and Gemini are called live per request; people using '
        'the app write reports and live-sharing sessions to SQLite through the backend.">'
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" class="ahead"/></marker></defs>' + "".join(f1) + "</svg>")

# ---------- Figure 2: one trip, browser to backend and back ----------
lanes = [("Browser", "app/web"), ("Backend", "FastAPI"), ("Corridor", "DuckDB + cache"),
         ("Google Maps", "Places · Routes · Tiles"), ("Gemini", "explanations"), ("SQLite", "user data")]
W2 = 1140
LX = [100, 300, 500, 700, 880, 1050]
B, K, D, G, M, S = range(6)
steps = [
    ("Search", [
        (B, K, "GET /api/search"),
        (K, D, "places + street names"),
        (K, G, "Autocomplete, restricted to the city"),
        (K, B, "suggestions: local first, then Google"),
        (B, K, "GET /api/google/place/{id}"),
        (K, G, "Place Details"),
        (K, B, "lat, lon"),
    ]),
    ("Plan", [
        (B, K, "POST /api/plan  from, to, time"),
        (K, D, "corridor → graph · districts"),
        (K, G, "Routes API: Google’s walking route"),
        (K, K, "score every 50 m · 5 signals · Plan B"),
        (K, B, "routes, scores, weak stretches, stops"),
    ]),
    ("Explain", [
        (B, K, "POST /api/explain"),
        (K, M, "facts JSON from the cached plan"),
        (M, K, "text → fact checks, 1 retry"),
        (K, B, "explanation, or 502 with the reason"),
    ]),
    ("On the map", [
        (B, K, "GET /api/google/tiles/…"),
        (K, G, "Map Tiles (proxied, not cached)"),
        (B, K, "GET /api/layers (zoom ≥ 14)"),
        (K, D, "lamps · lit streets · night lights"),
        (B, K, "GET /api/nearby"),
        (K, G, "Nearby, live open-now (leaving now)"),
    ]),
    ("Report and share", [
        (B, K, "POST /api/reports"),
        (K, S, "snapped to a street; plan cache refreshes"),
        (B, K, "POST /api/trips"),
        (K, S, "hashed links, 2 h expiry"),
        (B, K, "contact’s /live page polls every 5 s"),
    ]),
]
f2 = []
top, step_h, phase_gap = 96, 30, 26
y = top
rows = []
for phase, msgs in steps:
    py = y
    y += 34
    for m in msgs:
        rows.append((y, m))
        y += step_h
    rows.append(("band", phase, py, y))
    y += phase_gap
H2 = int(y + 10)
for item in rows:
    if item[0] == "band":
        _, phase, py, pe = item
        f2.append(f'<rect x="8" y="{py}" width="{W2 - 16}" height="{pe - py}" rx="8" class="band"/>')
        f2.append(text(18, py + 15, phase.upper(), "t eyebrow"))
for i, (name, sub) in enumerate(lanes):
    x = LX[i]
    f2.append(f'<line x1="{x}" y1="70" x2="{x}" y2="{H2 - 6}" class="life"/>')
    f2.append(box(x - 76, 14, 152, 50, "box head"))
    f2.append(text(x, 36, name, "t strong", "middle"))
    f2.append(text(x, 54, sub, "t small", "middle"))
n = 0
for item in rows:
    if item[0] == "band":
        continue
    yy, (a, b, label) = item
    n += 1
    xa, xb = LX[a], LX[b]
    if a == b:
        f2.append(f'<path d="M{xa},{yy - 6} h46 v16 h-42" class="edge" marker-end="url(#ah2)"/>')
        f2.append(text(xa + 54, yy + 6, label, "lbl", "start"))
    else:
        d = 1 if xb > xa else -1
        f2.append(f'<line x1="{xa + 4 * d}" y1="{yy}" x2="{xb - 6 * d}" y2="{yy}" class="edge{" ret" if d < 0 else ""}" marker-end="url(#ah2)"/>')
        lx = (xa + xb) / 2
        f2.append(text(lx, yy - 6, label, "lbl", "middle"))
    f2.append(f'<circle cx="{LX[a]}" cy="{yy}" r="3" class="dot"/>')
fig2 = (f'<svg viewBox="0 0 {W2} {H2}" role="img" aria-label="Sequence of one trip: search with local index and Google '
        'Autocomplete, plan with a DuckDB corridor plus Google’s walking route scored by SafeHer, explain with Gemini '
        'and fact checks, map tiles and nearby places from Google, and reports and live sharing stored in SQLite.">'
        '<defs><marker id="ah2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" class="ahead"/></marker></defs>' + "".join(f2) + "</svg>")

TABLE = [
    ("OpenStreetMap (Geofabrik)", "Build", "None", "On rebuild (daily extracts)", "segments, places, districts", "Walking network, lit streets and lamps (scored and drawn), open places, search, district lookup"),
    ("NASA Black Marble VNP46A4", "Build", "Earthdata token", "Yearly product", "night_lights", "Measured lighting where OSM has no tags or lamps; the night-glow map layer (areas, not single lamps)"),
    ("NCRB · India Data Portal", "Build", "None", "Yearly (latest: 2022)", "incidents", "Delhi police-district figure for each point on a route, low confidence"),
    ("Bengaluru City Police · OpenCity", "Build (planned)", "None", "Yearly", "city_stats", "City-wide trend shown as context, not scored"),
    ("Google Maps Platform", "Live", "API key", "Every request", "Nothing (place IDs only)", "Map, search, open-now, comparison route, Street View"),
    ("Gemini", "Live", "API key", "Every new trip", "Nothing", "Plain-language explanation after fact checks"),
    ("People using SafeHer", "Live", "None", "As they use it", "safeher.sqlite3", "Community reports, live-sharing sessions"),
]
rows_html = "".join("<tr>" + "".join(f"<td>{E(c)}</td>" for c in r) + "</tr>" for r in TABLE)

PAGE = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SafeHer Architecture</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* Layout: one reading column for prose, figures allowed full width with their own horizontal scroll. */
:root {{
  --bg: #F6F4F8; --surface: #FFFFFF; --ink: #1D1A22; --muted: #5D5866; --line: #D9D4E0;
  --violet: #5B2A86; --violet-soft: #EEE6F6; --amber: #B26A00; --amber-soft: #FBEFD9; --teal: #13786A; --teal-soft: #E0F2EF;
  --display: "IBM Plex Sans", system-ui, sans-serif; --body: "IBM Plex Sans", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #141218; --surface: #1D1A22; --ink: #EDEAF2; --muted: #A9A3B3; --line: #37323F;
  --violet: #C9A7EB; --violet-soft: #2D2338; --amber: #F2B65A; --amber-soft: #36291A; --teal: #5CC9B8; --teal-soft: #17302C; color-scheme: dark }} }}
:root[data-theme="dark"] {{
  --bg: #141218; --surface: #1D1A22; --ink: #EDEAF2; --muted: #A9A3B3; --line: #37323F;
  --violet: #C9A7EB; --violet-soft: #2D2338; --amber: #F2B65A; --amber-soft: #36291A; --teal: #5CC9B8; --teal-soft: #17302C; color-scheme: dark }}
body {{ background: var(--bg); color: var(--ink); font: 400 16px/1.6 var(--body);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 40px 64px; }}
main {{ max-width: 1180px; margin: 0 auto; display: grid; gap: 40px; }}
.prose {{ max-width: 68ch; display: grid; gap: 12px; }}
h1 {{ font: 600 2.1rem/1.15 var(--display); margin: 0; letter-spacing: -0.01em; text-wrap: balance; }}
h2 {{ font: 600 1.35rem/1.25 var(--display); margin: 0; text-wrap: balance; }}
p {{ margin: 0; }}
.meta {{ font: 500 .82rem/1.4 var(--mono); color: var(--muted); letter-spacing: .02em; }}
code {{ font: .88em var(--mono); background: var(--violet-soft); padding: 1px 5px; border-radius: 4px; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 8px 20px; font-size: .9rem; color: var(--muted); }}
.legend span {{ display: inline-flex; align-items: center; gap: 8px; }}
.sw {{ width: 26px; height: 14px; border-radius: 4px; border: 2px solid var(--muted); }}
.sw.dash {{ border-style: dashed; }} .sw.live {{ border-color: var(--teal); background: var(--teal-soft); }} .sw.bld {{ border-color: var(--amber); background: var(--amber-soft); }}
figure {{ margin: 0; display: grid; gap: 12px; }}
.scroll {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 12px; }}
.scroll svg {{ display: block; min-width: 900px; width: 100%; height: auto; color: var(--ink); }}
figcaption {{ color: var(--muted); font-size: .95rem; max-width: 80ch; }}
svg .box {{ fill: var(--surface); stroke: var(--line); stroke-width: 1.5; }}
svg .src {{ stroke: var(--amber); fill: var(--amber-soft); }}
svg .src-live {{ stroke: var(--teal); fill: var(--teal-soft); }}
svg .build {{ stroke: var(--amber); }}
svg .store {{ stroke: var(--violet); fill: var(--violet-soft); }}
svg .backend {{ stroke: var(--ink); stroke-width: 2; }}
svg .head {{ fill: var(--violet-soft); stroke: var(--violet); }}
svg .band {{ fill: var(--bg); stroke: none; }}
svg .life {{ stroke: var(--line); stroke-width: 1.5; stroke-dasharray: 4 5; }}
svg .edge {{ fill: none; stroke: currentColor; stroke-width: 1.5; }}
svg .edge.ret {{ stroke-dasharray: 5 4; }}
svg .ahead {{ fill: currentColor; }}
svg .dot {{ fill: currentColor; }}
svg .t {{ fill: currentColor; font: 400 13px var(--body); }}
svg .strong {{ font-weight: 600; font-size: 14px; }}
svg .small {{ fill: var(--muted); font-size: 12px; }}
svg .mono {{ font: 500 10.5px var(--mono); fill: var(--violet); }}
svg .tag {{ fill: var(--amber); font: 500 11px var(--mono); }}
svg .tag-live {{ fill: var(--teal); font: 500 11px var(--mono); }}
svg .eyebrow {{ fill: var(--muted); font: 600 11px var(--mono); letter-spacing: .12em; }}
svg .lbl {{ fill: currentColor; font: 400 12px var(--body); paint-order: stroke; stroke: var(--surface); stroke-width: 4px; stroke-linejoin: round; }}
table {{ border-collapse: collapse; width: 100%; min-width: 860px; font-size: .92rem; }}
th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ font: 600 .75rem/1.3 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }}
td:first-child {{ font-weight: 600; }}
.rules {{ display: grid; gap: 10px; padding: 0; margin: 0; list-style: none; max-width: 80ch; }}
.rules li {{ padding-left: 16px; border-left: 3px solid var(--violet); }}
.rules b {{ font-weight: 600; }}
</style>
<main>
  <header class="prose">
    <div class="meta">SafeHer · branch feature/google-integration · 16 Oct 2026</div>
    <h1>SafeHer Architecture</h1>
    <p>Where every piece of data comes from, how it is handled, and what happens between the browser and the backend for one trip. This is what is built and pushed as of 16 Oct: one read-only reference database built by one command, one database for what people create, and live calls for Google and Gemini.</p>
    <div class="legend" aria-label="Legend">
      <span><i class="sw bld"></i>fetched once by the build</span>
      <span><i class="sw live"></i>called live, nothing stored</span>
      <span><i class="sw dash"></i>database file, or planned</span>
    </div>
  </header>

  <figure>
    <h2>1 · Data sources and how each is handled</h2>
    <div class="scroll">{fig1}</div>
    <figcaption>Four sources are fetched once by the build pipeline into <code>safeher.duckdb</code>; the backend reads only a trip’s corridor from it. Google Maps and Gemini are called per request and nothing they return is kept. Reports and live-sharing sessions are the only data written while the app runs, into SQLite, which handles several server workers writing at once.</figcaption>
  </figure>

  <section class="prose" style="max-width:none">
    <h2>What each source is used for</h2>
    <div class="scroll" style="padding:0"><table>
      <thead><tr><th>Source</th><th>Fetched</th><th>Key</th><th>Refresh</th><th>Stored in</th><th>Used for</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>
  </section>

  <figure>
    <h2>2 · One trip, from the browser to the backend and back</h2>
    <div class="scroll">{fig2}</div>
    <figcaption>Solid arrows are requests, dashed arrows are replies. The plan is cached, so the explanation reuses it instead of planning again, and a new report refreshes it. The only live dependencies are Google and Gemini; everything else is local.</figcaption>
  </figure>

  <section class="prose">
    <h2>Rules the flow follows</h2>
    <ul class="rules">
      <li><b>No silent fallbacks.</b> If a configured key fails, or Gemini’s answer fails the fact checks twice, the request returns an error with the reason. A key that isn’t set simply turns that feature off.</li>
      <li><b>Google content only on a Google map.</b> Places and Routes results appear only when the Google basemap is on, as Google’s terms require.</li>
      <li><b>Keys stay on the server.</b> Map tiles and Street View images pass through the backend without being cached.</li>
      <li><b>Scores come from SafeHer’s rules.</b> Google’s route is scored by the same engine as SafeHer’s own routes; Gemini only puts the results into words.</li>
      <li><b>Supported cities only.</b> Delhi and Bengaluru are built locally; a trip elsewhere gets a clear message. Adding a city is one entry in <code>cities.json</code> plus the build command.</li>
    </ul>
  </section>
</main>
"""
(HERE / "architecture.html").write_text(PAGE)

# Standalone SVGs: same drawing, styles and light/dark colours embedded (GitHub shows them as images).
css = PAGE[PAGE.index("svg .box"):PAGE.index("table {")].replace("svg .", ".")
SVG_STYLE = ("<style>"
             "svg{--bg:#F6F4F8;--surface:#FFFFFF;--ink:#1D1A22;--muted:#5D5866;--line:#D9D4E0;--violet:#5B2A86;"
             "--violet-soft:#EEE6F6;--amber:#B26A00;--amber-soft:#FBEFD9;--teal:#13786A;--teal-soft:#E0F2EF;"
             "--body:system-ui,-apple-system,'Segoe UI',sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink)}"
             "@media (prefers-color-scheme: dark){svg{--bg:#141218;--surface:#1D1A22;--ink:#EDEAF2;--muted:#A9A3B3;"
             "--line:#37323F;--violet:#C9A7EB;--violet-soft:#2D2338;--amber:#F2B65A;--amber-soft:#36291A;--teal:#5CC9B8;--teal-soft:#17302C}}"
             ".canvas{fill:var(--surface)}" + css + "</style>")
for name, fig in (("data-sources.svg", fig1), ("trip-flow.svg", fig2)):
    head, body = fig.split(">", 1)
    vb = head.split('viewBox="')[1].split('"')[0]
    w, h = vb.split()[2:]
    svg = (head.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ') + ">" + SVG_STYLE
           + f'<rect class="canvas" width="{w}" height="{h}" rx="14"/>' + body)
    (HERE / name).write_text(svg)
print("wrote", ", ".join(p.name for p in sorted(HERE.glob("*.svg"))), "and architecture.html")
