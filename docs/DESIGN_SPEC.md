# SafeHer — Design Spec

**Status:** Draft v0.2 · **Event:** Google AI Builder Cup · **Track:** Sustainability & Social Welfare
**Last updated:** 4 Oct 2026

---

## 1. Summary

SafeHer is an AI-powered women's safety and mobility platform that helps users make better-informed travel decisions. Conventional navigation optimises for time and distance; SafeHer compares candidate routes on **street lighting, transit access, nearby safe spaces, historical incidents and recent community reports**, and uses **Gemini** to explain in plain language — in the user's own language — why one route may be preferable.

Users can also report hazards, share live trips with trusted contacts, and find the nearest **police station, public washroom, or well-lit public place** at any point on their journey. Aggregated, privacy-preserving insights help communities and authorities see where safety gaps are and prioritise fixes.

> **Core principle:** SafeHer supports *safer decisions*. It never claims a route is "safe" or guarantees safety. All language in the product says "safer", "better lit", "more support nearby" — never "safe".

---

## 2. Problem

- Navigation apps pick the fastest route, which at night is often a dark shortcut, an empty service road or a long walk from transit.
- Women already make these trade-offs mentally ("I'll take the main road even though it's longer"), but with incomplete information, especially in unfamiliar areas.
- Basic needs on a trip — a clean washroom, a place to wait that is lit and staffed, the nearest police station — are hard to find quickly when it matters.
- Safety data that does exist (crime statistics, lighting audits, citizen complaints) is scattered, coarse, and rarely reaches the person making a decision at 10 pm.
- Authorities lack a fine-grained, continuously updated picture of *where* people feel unsafe and *why*.

## 3. Goals and non-goals

**Goals (MVP)**
1. Show 2–3 candidate routes with a transparent, explainable safety score each.
2. Combine five real data signals into that score using published, rule-based weights.
3. Let Gemini explain the comparison and answer follow-up questions in multiple languages.
4. Surface essential **safe stops** along the route: police stations, public washrooms, well-lit / staffed places, hospitals, pharmacies.
5. Let users report hazards in under 15 seconds.
6. Let users share a live trip with trusted contacts.
7. Give communities and authorities an aggregated hotspot dashboard.

**Non-goals (MVP)**
- Predicting crime or labelling neighbourhoods "dangerous".
- Replacing emergency services — SafeHer always routes emergencies to **112** (and shows 181 / 1091 women's helplines).
- An ML-trained scoring model (scoring is deliberately rule-based and auditable for v1).
- Turn-by-turn voice navigation (we hand off to Google Maps for that).

## 4. Users

| Persona | Context | Key need |
|---|---|---|
| **Priya, 22, student** | Commutes by metro + walk; late classes | "Which exit and which road should I walk at 9 pm?" |
| **Meera, 34, working professional** | New to the city, uses cabs and walking | "Is the walk from the bus stop to my PG okay? Where's a washroom on the way?" |
| **Fatima, 45, parent** | Daughter travels alone | Receive trip shares, get alerted if the trip stalls |
| **Ward officer / NGO analyst** | Plans streetlights, patrols, toilets | "Where are reports clustering and what are they about?" |

## 5. MVP feature spec

### 5.1 Route comparison map
- User enters origin (defaults to current location) and destination; picks mode: **Walk**, **Transit + walk**, **Two-wheeler/cab drop-off + walk**.
- Departure time defaults to "now"; user can choose a future time (the score is **time-aware** — lighting matters after sunset, shop/safe-space opening hours change).
- System fetches 2–3 candidate routes and draws them on the map, colour-coded by score band.
- Each route card shows: duration, distance, **safety score (0–100)**, top 2 reasons ("Well lit 92% of the way", "Passes 3 open safe spaces").
- Tapping a route expands the **score breakdown** (one bar per signal) and highlights weak segments on the map (e.g. a 200 m unlit stretch).
- "Start" hands off to Google Maps for navigation and (optionally) starts trip sharing.

### 5.2 Transparent safety score
See [§7 Scoring model](#7-scoring-model). Every number shown to the user can be traced to a signal, a weight, and a data source, and the "How is this calculated?" sheet shows exactly that.

### 5.3 Five data signals
1. **Lighting** — street lamp coverage + night-time light intensity along the route.
2. **Transit proximity** — distance to active metro/bus stops, and frequency at departure time.
3. **Verified safe spaces** — police stations, hospitals, pharmacies, 24×7 stores, fuel stations, women-help desks, open at travel time.
4. **Historical anonymised incidents** — aggregated official/open data, coarse grid only.
5. **Recent community reports** — last 30 days, decayed by age, weighted by verification.

### 5.4 Gemini route explanation and multilingual Q&A
- After routes load, Gemini generates a 2–3 sentence comparison grounded **only** in the computed score breakdown and segment data (structured JSON passed in the prompt).
  - Example: *"Route B takes 4 minutes longer but stays on MG Road, which is well lit and passes two 24-hour pharmacies. Route A cuts through a 300 m lane with no recorded street lighting and two recent reports of poor lighting."*
- Chat sheet for follow-ups: "Is there a washroom near the metro exit?", "Which route has more people around at 10 pm?", "Explain in Hindi".
- Languages at launch: **English, Hindi, Marathi, Tamil, Telugu, Kannada, Bengali** (Gemini handles generation; UI strings localised).
- Voice input for Q&A (speech-to-text) for hands-free use.
- **Guardrails:** Gemini may not invent facts, never says "safe", must cite which signal drives a claim, and must surface 112 if the user describes an emergency. Uses function calling to fetch nearby places rather than recalling them.

**Implemented (13 Oct 2026): explanation step** (`backend/safeher/explain.py`, `POST /api/explain`)
- **Facts only:** the plan is reduced to a fact sheet (per route: minutes, km, score, band, signal values with confidence, reasons, weak stretches, named stops; plus departure time and data notes). The model sees only this JSON.
- **Providers:** `SAFEHER_LLM=auto` (default) uses **Gemini** (`GEMINI_API_KEY` in `backend/.env`; model `GEMINI_MODEL`, default `gemini-2.5-flash`), then the **template**. A local model through Ollama is opt-in (`SAFEHER_LLM=ollama`). `SAFEHER_LLM=template` forces the template.
- **Guardrail check on every answer, from any provider.** An answer fails if it:
  - uses "safe", "safest", "unsafe", "dangerous", "guarantee" or "risk-free". In Hindi, bare सुरक्षित, असुरक्षित, खतरनाक and गारंटी are banned; "अधिक सुरक्षित" (safer) is allowed.
  - contains any number not in the facts (or simple differences between them, the departure time, 100 or 112).
  - fails to state the scores of the routes it compares.
  - names a place in the area that isn't in the facts. Trip start and end landmarks are allowed.
  - is in the wrong language, mixes scripts inside a word, or uses unexpected scripts.
  - runs longer than about three sentences.
- A failing answer is retried once with the problems listed. If it still fails, the template is used. The template passes the check in all 20 tested cases (2 cities × 5 times × 2 languages).
- **Languages:** English and Hindi at launch for explanations (the other spec languages to follow).
- **UI:** an explanation card above the route cards with an EN | हिं toggle. It labels its source ("Explained by Gemini" / "Summary from route data") and shows "Checked against this route's data".
- **Finding:** in testing, the local `phi3:mini` answers failed in English (used "safe", invented a number). Its Hindi passed the original checks but was unusable: mixed scripts, Cyrillic letters, wrong route times, 6–12 s per answer. The script, mixed-word and must-state-scores checks were added because of this, and the local model was made opt-in.
- **Not yet:** the "Ask SafeHer" chat (function calling, 112 on emergency) and voice input.

### 5.5 Safe stops — police, washrooms, well-lit places (added from team discussion)
A dedicated layer and quick-action bar for essential stops **on or near the planned route**, not just near the user:

| Quick action | What it shows | Data |
|---|---|---|
| 🚓 **Nearest police station** | Nearest stations / police booths / pink booths, distance, call button | Google Places (`police`), OSM `amenity=police`, state police open data |
| 🚻 **Closest washroom** | Public toilets, malls, metro stations, fuel stations with toilets; women-friendly / open-now flags | Google Places, OSM `amenity=toilets`, SBM / city toilet locator data, community ratings |
| 💡 **Well-lit places** | Places to wait or regroup: lit, staffed, open now (24×7 stores, hospitals, metro stations, fuel stations, cafés) | Places + opening hours + lighting signal |
| 🏥 **Hospital / pharmacy** | Nearest open | Google Places |
| 🆘 **SOS** | One-tap call 112; shows 181 / 1091 | Static |

- **Trip planning mode:** when planning, user can toggle "Show stops along route" to see these pinned along each candidate route, and the route card shows e.g. "Washroom at 1.2 km (Metro station)".
- **Add a stop:** user can insert a washroom or well-lit waiting point as a waypoint; routes recompute.
- Community can rate washrooms (clean, lit, women's section, staffed) — these ratings feed back into safe-space verification.

### 5.6 Community hazard reporting
- Floating "Report" button → pick category → auto-captured location/time → optional photo + note → submit. Target: **< 15 seconds**.
- Categories: Broken / no streetlight · Harassment experienced · Unsafe crowd / loitering · Isolated stretch · Poor footpath · Unsafe washroom · Positive: well-lit / helpful place.
- Anonymous by default; location snapped to the road segment, not stored as an exact home-adjacent point.
- Reports appear on the map (clustered) for 30 days with decaying weight.
- Verification: corroborated by ≥3 independent reports, or confirmed by a partner (NGO / municipal), or matches a public data source.
- Gemini assists moderation: classifies free text, flags personal info (names, number plates) for redaction, detects spam/abuse.

### 5.7 Trusted-contact trip sharing
- User adds up to 5 trusted contacts (phone/email).
- "Share trip" sends a link with live location, route, and ETA. Contacts don't need the app (web view).
- Auto-checks: if the user goes **off-route > 200 m** or is **stationary > N min** unexpectedly, or ETA passes by > 10 min, SafeHer prompts the user ("Everything okay?"). No response in 60 s → alert contacts.
- Arrival → contacts get "Reached safely ✓"; location sharing stops and live data is deleted after 24 h.
- SOS within a trip sends location to all contacts and offers to call 112.

### 5.8 Aggregated hotspot dashboard
- Web dashboard for community groups, NGOs and municipal partners.
- H3 hexagon grid (resolution ~8–9, ≈0.1–0.7 km²) showing report density by category and time of day; trend over weeks.
- Lighting gap map: road segments with high foot-traffic routes but low lighting score → candidate streetlight locations.
- Washroom gap map: long route stretches with no accessible women's washroom.
- **Privacy:** k-anonymity threshold (hide cells with < 5 reports), no individual reports or trips, no raw coordinates exported.
- Gemini generates a weekly plain-language summary: "Reports of broken lights near X rose 40%; 70% are between 8–11 pm."

## 6. User experience

### 6.1 Key flows
1. **Plan a trip:** Home → Where to? → routes with scores → tap route → breakdown + Gemini explanation → (optional) add stop → Start + Share trip.
2. **Quick need on the move:** Home → quick-action bar → "Washroom" → list/map of nearest open → Navigate.
3. **Report:** Report button → category → submit → thank-you + "this helps others".
4. **Trusted contact:** receives link → live map → arrival notice.
5. **Authority:** Dashboard → filter area/time/category → export aggregated CSV / share summary.

### 6.2 Screens (mobile-first)
| Screen | Key elements |
|---|---|
| **Onboarding** | 3 cards: what SafeHer does, what it doesn't (no guarantees), permissions (location, contacts, notifications) — each optional with explanation; language picker first |
| **Home / Map** | Search bar, current location, quick-action bar (🚓 🚻 💡 🏥), SOS button (always bottom-right, red), Report FAB |
| **Route comparison** | Bottom sheet with 2–3 route cards; map with colour-coded routes; time picker |
| **Route detail** | Score ring, 5 signal bars, weak segments list, Gemini explanation, "Ask SafeHer" chat entry, stops along route, Start / Share buttons |
| **Ask SafeHer (chat)** | Gemini chat, voice input, suggested questions, language switch |
| **Safe stops list** | Filter chips (open now, women's toilet, 24×7), distance, call/navigate |
| **Report** | Category grid, photo, note, anonymous toggle (on by default) |
| **Active trip** | Mini-map, ETA, "contacts watching" indicator, I'm safe / SOS buttons |
| **Contacts** | Trusted contacts list, test alert |
| **Dashboard (web)** | Hex map, filters, trends, gap maps, AI weekly summary |

### 6.3 Visual design
- **Tone:** calm, confident, not fear-inducing. No red-alert overload; red reserved for SOS only.
- **Palette (tokens):**
  - `primary` Deep violet `#5B2A86` — brand, buttons
  - `accent` Warm amber `#F2A541` — lighting / highlights
  - `score-high` Teal `#1F9E89` · `score-mid` Amber `#E0A526` · `score-low` Slate-rose `#C2577A` (avoid pure red for scores)
  - `sos` Red `#D62828` — SOS only
  - Neutrals: `#1B1B1F` text, `#F7F5FA` background; full dark mode (dark mode is default after sunset)
- **Score bands:** 75–100 "More support", 50–74 "Mixed", 0–49 "Less support" — wording avoids "safe/unsafe".
- **Type:** Google Sans / Noto Sans (Noto covers all Indic scripts) — 16 px minimum body.
- **Map style:** custom Google Maps style that dims non-essential POIs and emphasises lit roads (amber glow) and safe stops.
- **Accessibility:** WCAG 2.2 AA contrast; 48 dp touch targets; colour never the only signal (icons + labels on scores); screen-reader labels; works one-handed.
- **Discreet mode:** neutral app name/icon option and quick-exit gesture that switches to a blank map.

### 6.4 Demo web app (implemented: `app/web/`, served by `backend/safeher/api.py`)
- **Stack:** plain HTML, CSS and JS with no build step (Node isn't needed). **MapLibre GL** with free **CARTO** basemaps: *Voyager* by day, *Dark Matter* when the departure is after sunset. Roboto + Material Symbols. The API serves the page at `/`, so the demo is one command.
- **Layout (Google Maps pattern):** floating directions panel on the left (bottom sheet on phones ≤ 760 px), category chips across the top (Police, Washrooms, Open now, Hospitals, Pharmacies), Report and SOS buttons bottom-right, theme toggle (auto / dark / light).
- **Routes on the map:** the selected route is coloured stretch by stretch by score (rose → amber → teal). At night, stretches with *measured* lighting get an amber glow and mapped street lamps show as amber dots. Alternatives are grey and clickable. Time and score bubbles avoid colliding on screen.
- **Panel:** a comparison line ("Most support takes N min longer and scores X vs Y"), route cards (score ring, band, label, two data-only reasons, weak-stretch count), and for the selected route: per-signal bars with confidence, weak stretches (tap to highlight), stops along the route, **Start in Google Maps** (walking hand-off with 3 waypoints to keep the chosen path), **Copy route link** (state lives in the URL hash) and **How is this calculated?** (live weights for the chosen time, formula, bands, limits).
- **Departure-time slider (00:00–23:45, 15-min steps):** re-scores as you drag, which shows lighting weight, closing hours and the day/night map switch live. The demo opens at 9:30 PM.
- **Reporting:** category grid, then tap the map. Anonymous, snapped to the nearest street node, pending (counts half). Routes re-score immediately.
- **API:** `GET /api/areas`, `/api/meta`, `/api/search`, `/api/nearby`, `/api/layers/{area}`, `/api/reports`; `POST /api/plan`, `/api/reports`. Reports are stored in `backend/var/reports.jsonl` (gitignored; Firestore later).

## 7. Scoring model

### 7.1 Approach
Rule-based and transparent. Each route is split into **~50 m segments**. Each segment gets a 0–100 sub-score per signal; the route score is the **length-weighted average** of segment scores, with a **penalty for the worst stretch** (a route that is great except for one dark 300 m lane shouldn't average that away).

```
segment_score = Σ (weight_i × signal_i)                     # weights sum to 1
route_score   = 0.8 × length_weighted_mean(segment_score)
              + 0.2 × percentile_10(segment_score)           # worst-stretch term
```

### 7.2 Signals and weights

| Signal | Night weight | Day weight | How a segment is scored (0–100) |
|---|---|---|---|
| Lighting | **0.30** | 0.05 | OSM `lit=yes` / street-lamp density within 25 m; fallback: VIIRS night-light radiance percentile for the city. Community "broken light" reports subtract. |
| Transit proximity | 0.15 | 0.20 | Distance to nearest active stop at departure time (≤150 m = 100, ≥800 m = 0), boosted by service frequency. |
| Verified safe spaces | 0.25 | 0.30 | Count of safe spaces **open at that time** within 150 m, saturating at 3. Police/hospital weighted ×1.5. |
| Historical incidents | 0.15 | 0.20 | Inverse of normalised incident density in the segment's H3 cell (city-relative percentile). Coarse by design. |
| Recent community reports | 0.15 | 0.25 | Starts at 100; each report within 50 m subtracts by category severity × verification × age decay (half-life 7 days). Positive reports add. |

"Night" = sunset to sunrise for the city/date (computed). Between, weights interpolate over 30 min.

### 7.3 Data confidence
- Each signal also returns a **confidence** (0–1) based on data coverage. Low-coverage signals have their weight redistributed and the UI shows "Limited data in this area" rather than a misleadingly precise score.
- Score shown as a whole number plus band; detail view shows the confidence of each signal.
- **Implemented rules** (`backend/safeher/scoring.py`, `signals.py`, weights version `2026-10-04.1`):
  - Effective weight of a signal = base weight × confidence, renormalised to sum to 1. "No data" (confidence 0) therefore never counts as a low score.
  - A segment whose confidence-backed weight is < 15% of the total is **unscored**; a route with < 50% of its length scored shows "Limited data" instead of a number.
  - The worst-stretch term is a **length-weighted** 10th percentile, so short segments can't hide a long dark stretch.
  - Night/day blend is linear over 30 min **centred** on sunrise and sunset (`astral`); departure times must carry a timezone.

| Signal | Confidence used |
|---|---|
| Lighting: OSM `lit=*` tag | 0.9 (`limited`/`interval` = 70; `lit=no` = 0 at night, 50 by day) |
| Lighting: mapped street lamps within 25 m | 0.7 (60 + 20 per lamp, max 100) |
| Lighting: no tag or lamps → road-type prior (primary 80 … path 25) | 0.3, labelled "estimated" in UI |
| Transit | 0.8 (0.6 when no stop is found in the search radius) |
| Safe spaces | 0.7 |
| Historical incidents | Set per city; low (≈0.2) while only district-level data exists |
| Community reports | 0.4 with no reports (absence is weak evidence), rising 0.1 per report to 0.9 |

- Report severity (points subtracted when fresh and verified): harassment 40 · unsafe crowd 25 · isolated stretch 20 · broken light 15 · poor footpath 10 · unsafe washroom 10 · positive −15 (adds). Pending reports count ×0.5; reports > 30 days old or future-dated are ignored. Broken-light reports affect only the reports signal, not lighting, to avoid double counting.

### 7.4 Fairness rules
- Incident data is never shown as a standalone "danger map" to users; it only contributes inside route scores.
- Scores are city-relative percentiles to avoid labelling entire low-income areas as low-scoring because of under-investment; the dashboard frames gaps as *infrastructure gaps* (lighting, toilets, transit), not as "bad neighbourhoods".
- Weights are published in-app and in this repo; changes are versioned.

## 8. Data sources

| Signal / feature | Primary source | Fallback / enrichment | Freshness |
|---|---|---|---|
| Base map, routing | Google Maps Platform — Maps SDK, **Routes API** (request alternative routes; if a mode returns only one, generate alternates via waypoint perturbation) | — | Live |
| Places (police, washrooms, hospitals, 24×7 stores) | Google **Places API (New)** incl. opening hours | OpenStreetMap (`amenity=police/toilets/hospital/pharmacy`) | Live / weekly |
| Lighting | OpenStreetMap `lit=*`, `highway=street_lamp` | NASA **VIIRS Black Marble** night-time lights; municipal streetlight datasets where published; community reports | Monthly |
| Transit | **GTFS** static + realtime where available (e.g. Delhi Open Transit Data; city metro/bus GTFS feeds) | Places `transit_station` | Live / weekly |
| Historical incidents | **NCRB** "Crime in India" (district level) and state/city police open data via **data.gov.in** | — | Annual (coarse) |
| Public toilets | City / Swachh Bharat toilet locator data where available, OSM, Places | Community ratings | Weekly |
| Community reports | SafeHer users (Firestore) | Partner NGO audits | Real time |
| Sunrise/sunset | Computed from lat/lng/date | — | Computed |

> Data availability varies by city. The MVP targets **one pilot city** with the best open-data coverage (decide in §13) and shows coverage/confidence everywhere else.

### 8.1 Hackathon MVP: open-source data stack (decided 4 Oct 2026)
For the MVP the backend runs on open data with no paid API keys. Google services are optional adapters that can be switched on later.

| Need | MVP source | Notes |
|---|---|---|
| Walking routes + alternatives | **OpenStreetMap walking graph** (OSMnx) routed locally | Generates 2–3 alternatives itself (penalised shortest paths) and can search for a better-supported route, not just score given ones. Avoids Routes API often returning a single walking route. |
| Lighting | OSM `lit=*` on graph edges + `highway=street_lamp` nodes | VIIRS later |
| Police, washrooms, hospitals, pharmacies, 24×7 | OSM via **Overpass**, incl. `opening_hours` | |
| Transit stops | OSM bus stops, metro/rail stations | GTFS later |
| Historical incidents | NCRB district-level table | Low confidence; nearly constant across routes in a city, so the UI says so |
| Explanations / Q&A | Pluggable LLM: **Gemini** (free AI Studio key) → local **Ollama** → template fallback | Guardrail checks run on every output regardless of provider |
| Sunrise/sunset | `astral` | |

- **Cached snapshots:** public Overpass servers time out often, so each demo area is downloaded once (with mirror fallback) into `data/` and the API serves from that cache. The demo never depends on a live third-party server.

### 8.2 Demo areas and measured data coverage (fetched 7 Oct 2026)
Rebuild with `python -m safeher.fetch` (from `backend/`). Snapshots are committed in `data/areas/` (~9 MB) so the demo works offline.

| Area | Walkable network | Streets tagged `lit=*` | Street lamps mapped | Places with `opening_hours` |
|---|---|---|---|---|
| `blr-indiranagar-mg-road` (Bengaluru) | 700 km | 10% | 589 | 5% of 2,120 |
| `del-hauz-khas-saket` (Delhi) | 526 km | 0.1% | 0 | 15% of 667 |

**Consequences:**
- Delhi lighting is almost entirely the road-type estimate (confidence 0.3). A real lighting source (VIIRS night lights, or municipal streetlight data) is the most valuable data addition, especially for Delhi.
- Unknown opening hours use **typical hours by type**, flagged as assumed in the API output (`hours_assumed`): police and hospitals 24/7 · metro 05:30–23:30 · fuel 06–23 · pharmacy, convenience, supermarket 08/09–22 · mall 10–22 · café and restaurant 11–23 · washroom 06–22. Scores drop sharply after 23:00 mostly because of these assumptions, and the UI must say so.
- Historical incidents: see §8.4.

### 8.4 Historical incidents: NCRB (implemented: `backend/safeher/ncrb.py`)
- **Source:** NCRB *Crime in India*, district-wise crimes against women 2017–2022 (CSV from India Data Portal). The raw file goes in `data/raw/ncrb/` (gitignored). `python -m safeher.ncrb` builds `data/incidents/ncrb_delhi_police_districts.json` (committed).
- **Heads used (public spaces only):** assault on women (IPC 354), insult to modesty (509), attempt to rape, kidnapping/abduction (others). Domestic heads (cruelty by husband, dowry, DV Act) and cyber crimes are excluded.
- **Years:** average of 2019, 2021 and 2022. 2020 is skipped because of lockdown distortion.
- **Units:** Delhi's 15 territorial police districts. Non-geographic units (Crime Branch, EOW, IGI Airport, Metro, Railway, Special Cell, SPUWAC, Vigilance) are excluded. The value is the city-relative percentile of average cases. The demo area Hauz Khas–Saket is mapped to **South** district (196 cases/year, 11th of 15, where 1 = fewest), assuming the whole area falls in South district.
- **Bengaluru:** NCRB reports "Bengaluru City" as a single unit, so there's nothing to compare within the city. The signal stays "no data" (weight redistributed) rather than a constant.
- **Confidence 0.15:** raw counts with no population adjustment, registered cases reflect reporting rates, and there is one value per district. Because every route in an area gets the same value, it **never changes which route ranks first**. It only sets context, and the API says so in `data_notes`.
- **Upgrade path (incidents):** Delhi police station boundaries (DataMeet / GSDL GeoJSON, 180 stations) would allow per-point district lookup. Safetipin or Safecity data (on request) would give street-level signals.

### 8.5 Lighting fallback: NASA VIIRS night lights (implemented: `backend/safeher/viirs.py`; data pending)
- **Product:** NASA Black Marble **VNP46A4** (yearly, snow-free, near-nadir composite, ~500 m pixels), fetched with the World Bank's `blackmarblepy` (MIT). Tiles h25v06 (Delhi) and h25v07 (Bengaluru), cropped to each demo area and saved as `data/areas/<area>/viirs.npz`. The raw HDF5 files go in `data/raw/viirs/` (gitignored).
- **Access:** a free NASA Earthdata account. Store the token as `EARTHDATA_TOKEN` in `backend/.env` (gitignored), never in code, chat or commits. Fetch with `python -m safeher.viirs`.
- **Lighting precedence** (first available wins):

| Source | Value | Confidence |
|---|---|---|
| OSM `lit=*` tag | 100 / 70 / 0 | 0.9 |
| Mapped street lamps within 25 m | 60 + 20 per lamp | 0.7 |
| **VIIRS radiance, percentile within the demo area** | 0–100 | **0.45** |
| Road-type prior | 15–80 | 0.3 |

- **Limits:** VIIRS measures upward light at ~500 m. It separates dark parks and campuses from lit arterials, but can't see a single dark lane between lit buildings, so it never overrides OSM tags or lamps. Bright commercial areas (malls, signage) read as lit even where streets aren't. The UI groups VIIRS with "estimated" lighting.
- **Check:** a real-data test asserts Hauz Khas Deer Park is darker than Aurobindo Marg at Hauz Khas metro.

### 8.3 Routing (implemented: `backend/safeher/routing.py`, `plan.py`)
- Walking graph from OSMnx (`network_type="walk"`). Origin and destination snap to the nearest street node within 300 m.
- Up to three candidates:
  1. **Shortest** by length.
  2. **Most support:** street cost = length × (1 + λ·(1 − support/100)), trying λ = 3 → 1.5 → 0.75 until the result is within the detour limit. Support is each street's score at the departure hour (cached per area and hour).
  3. **Alternative:** streets already used cost 2× (then 4×) to force a genuinely different option.
- Any candidate more than **1.5×** the shortest length, or sharing more than **80%** of its length with an existing one, is dropped. Results are sorted by score.
- Each route is split into ~50 m pieces. Signals are read at each piece's midpoint: lamps within 25 m, open transit within 800 m, open safe spaces within 150 m, reports within 50 m.
- **Safe-space kinds** (when open): police, hospital, clinic, pharmacy, fuel, convenience, supermarket, mall, café, restaurant/fast food. Banks and ATMs are fetched but not counted.
- Output per route: score, band, coverage, per-signal breakdown with confidence, reasons (generated from data only), **weak stretches** (≥ 100 m scoring < 50, with their main factors), and **safe stops** (nearest police, washroom, hospital and pharmacy within 300 m of the route, with distance along it). Walking speed is 80 m/min.
- Measured: < 1 s for the first plan in an area, ~0.05 s after.


## 9. Architecture (Google stack)

```
┌────────────────────┐     ┌───────────────────────────┐
│ Mobile app         │     │ Dashboard (web)           │
│ Flutter (Android   │     │ Flutter Web / Next.js     │
│ first) + Maps SDK  │     │ + deck.gl H3 layer        │
└─────────┬──────────┘     └────────────┬──────────────┘
          │ Firebase Auth (anon + phone)  │
          ▼                               ▼
┌──────────────────────────────────────────────────────┐
│ API — Cloud Run (Python FastAPI)                     │
│  /routes/score   /places/nearby   /reports  /trips   │
│  /ask (Gemini)   /dashboard/aggregates               │
└───┬───────────┬───────────────┬──────────────┬───────┘
    │           │               │              │
    ▼           ▼               ▼              ▼
 Routes &    Gemini API /    Firestore      BigQuery (+GIS)
 Places API  Vertex AI       (reports,      (segments, lighting,
             (explain, Q&A,  trips, contacts) incidents, H3 aggregates)
              moderation)        │
                                 ▼
                        Cloud Functions / FCM
                        (trip alerts, push, SMS fallback)
```

- **Precomputation:** city road network → 50 m segments in BigQuery GIS with lighting, incident cell, and static safe-space counts precomputed nightly (Cloud Scheduler). Request time only adds time-dependent parts (open-now, transit frequency, recent reports).
- **Latency target:** route scores in **< 2 s** for 3 routes; Gemini explanation streamed after.
- **Gemini usage:**
  1. *Explain* — structured score JSON → short comparison (fast Flash-class model).
  2. *Ask* — chat with function calling: `get_route_breakdown`, `find_nearby(type, open_now)`, `get_reports(area)`.
  3. *Moderate* — classify reports, redact PII.
  4. *Summarise* — weekly dashboard narrative.
- **Offline:** cache last planned route, nearest safe stops and helplines; SOS works offline via native dialler/SMS.

## 10. Data model (Firestore, simplified)

```
users/{uid}            { lang, contacts[], settings }
reports/{id}           { category, segmentId, h3, ts, photoUrl?, text?, anon: true,
                         verification: pending|corroborated|partner, weight }
trips/{id}             { uid, route, startedAt, eta, status, contactsNotified[], expiresAt }
places_feedback/{id}   { placeId, type, clean, lit, womenSection, staffed, ts }
```
BigQuery: `segments`, `segment_static_scores`, `incident_cells`, `report_aggregates_h3`.

## 11. Privacy, safety and ethics

- **Data minimisation:** reports are anonymous by default; exact coordinates snapped to road segments; trip location kept only during the trip + 24 h.
- **No sale or sharing** of individual data. Authorities see only aggregates above the k-anonymity threshold.
- **Consent** for each permission with plain-language reasons; app works without location (manual search).
- **India DPDP Act 2023** alignment: purpose limitation, consent notices, data deletion on request.
- **Abuse resistance:** rate-limit reports per device, require corroboration before reports move scores significantly, detect coordinated spam.
- **Honest UX:** persistent footnote "Scores reflect available data and can't guarantee safety. In an emergency call 112."
- **Bias review:** check score distribution across neighbourhoods before launch; document limitations.

## 12. Success metrics (demo + pilot)

| Metric | Target |
|---|---|
| Route scored and explained | < 2 s scores, < 5 s explanation |
| Time to submit a report | < 15 s median |
| Users choosing a higher-scored route when it's ≤ 5 min longer | > 50% |
| Find nearest open washroom / police station | ≤ 2 taps |
| Explanation faithfulness (manual eval of 50 samples) | 100% claims traceable to data |
| Dashboard cells shown meet k ≥ 5 | 100% |

## 13. Open questions
1. ~~**Pilot city**~~ — **Decided: Delhi and Bengaluru** (one demo area each).
2. Flutter vs. native Android + web for the hackathon timeline? — **Deferred:** backend first; frontend chosen after the API works.
3. Partner NGO / municipal body for report verification and the dashboard demo?
4. SMS fallback for trip alerts — budget for an SMS provider or push-only for MVP?
5. Final name / discreet-mode icon.

## 14. Build plan (suggested)

| Phase | Scope |
|---|---|
| **Week 1** | Data ingestion for pilot city (OSM, VIIRS, GTFS, NCRB → BigQuery); segment scoring; Routes + Places integration; route comparison UI |
| **Week 2** | Gemini explain + Q&A; safe stops layer (police, washroom, well-lit); reporting; trip sharing |
| **Week 3** | Dashboard; privacy hardening; multilingual polish; demo script and video |

> **Revised 4 Oct 2026:** submission is under a week away. Priority order: (1) scoring engine + tests ✅, (2) OSM data cache + routing for Delhi and Bengaluru ✅, (3) safe stops along the route ✅, (4) explanation with guardrail tests ✅, (5) API ✅, (6) demo web app ✅. Dashboard, trip sharing and reporting UI only if time allows.

## 15. Team notes
- Feature additions from 1 Oct discussion: **nearest police station** (Shreya), **closest washroom** and **well-lit places when planning a trip** — incorporated in §5.5.

## 16. Decision log
| Date | Decision | Why |
|---|---|---|
| 4 Oct 2026 | Pilot cities: **Delhi + Bengaluru** | Best mix of transit open data (Delhi) and OSM lighting coverage (Bengaluru) |
| 4 Oct 2026 | **Backend first** (Python 3.12, FastAPI); frontend later | Scoring is the core claim and testable without keys or devices |
| 4 Oct 2026 | **Open-source data stack** for MVP (§8.1) | No API keys/billing needed; Google adapters optional |
| 4 Oct 2026 | Local OSM routing instead of Routes API | Guaranteed alternatives; lighting tags available per street |
| 4 Oct 2026 | Cache OSM snapshots per demo area | Public Overpass servers frequently time out |
| 4 Oct 2026 | Confidence-weighted scoring; "no data" ≠ low score (§7.3) | Avoids penalising unmapped areas, which skews against under-mapped neighbourhoods |
| 4 Oct 2026 | Scoring covered by unit tests incl. spec invariants (worst-stretch, decay, bands without "safe") | "Make sure it works correctly" |
| 7 Oct 2026 | Demo areas: Indiranagar–MG Road (BLR), Hauz Khas–Saket (DEL); snapshots committed | Reproducible offline demo |
| 7 Oct 2026 | Typical-hours fallback for places without `opening_hours`, flagged as assumed (§8.2) | Only 5–15% of places have hours; skipping them would make every night score near zero |
| 7 Oct 2026 | "Most support" route found by support-weighted cost, capped at 1.5× shortest (§8.3) | Shows a better option instead of only grading given ones; caps unrealistic detours |
| 7 Oct 2026 | Restaurants and cafés count as safe spaces when open | Staffed, lit and busy; matches §5.5 "well-lit places" |
| 7 Oct 2026 | NCRB incidents added for Delhi (15 police districts, public-space heads, 2019/21/22 average, confidence 0.15); Bengaluru stays "no data" (§8.4) | Real, open, citable; honest about being district-level and count-based |
| 7 Oct 2026 | Lighting source for Delhi (VIIRS) flagged as top data gap | Delhi has ~0% lit tags and no mapped lamps |
| 7 Oct 2026 | VIIRS VNP46A4 yearly via `blackmarblepy` as lighting fallback at confidence 0.45, below OSM tags and lamps (§8.5) | Measured beats guessed; yearly composite avoids cloud and moon noise; 500 m too coarse to override street-level data |
| 13 Oct 2026 | Demo frontend is a **web app** (MapLibre + CARTO, no build step) instead of Flutter; Flutter stays the post-hackathon target | Node and Flutter aren't installed, the deadline is days away, a web app runs on any phone and laptop for judges, and trip-share links need a web view anyway |
| 13 Oct 2026 | Map coloured per ~50 m stretch by score; amber glow only for *measured* lighting | Makes "this stretch is weaker" visible without overstating estimated lighting |
| 13 Oct 2026 | Hand-off to Google Maps for navigation with waypoints | Spec §3 non-goal (no turn-by-turn); waypoints keep the chosen route |
| 13 Oct 2026 | Explanations: Gemini → template; every answer fact-checked; local model opt-in (§5.4) | Scores stay rule-based; AI only words them. Template guarantees a faithful answer when Gemini is off or fails |
| 13 Oct 2026 | Explanation card replaces the static comparison line | Same content; avoids saying it twice |
