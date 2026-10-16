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
- A failing answer is retried once with the problems listed. If it still fails, the user sees an error (no silent template fallback, since 16 Oct). The template is used only when no AI provider is configured. The template passes the check in all 20 tested cases (2 cities × 5 times × 2 languages).
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
- **API:** `GET /api/cities`, `/api/meta`, `/api/search?city=`, `/api/nearby`, `/api/layers?south&west&north&east` (≤ 0.05° × 0.05°), `/api/reports?city=`; `POST /api/plan`, `/api/explain`, `/api/reports`, `/api/trips`. Trips outside the supported cities get **422** with a message the app shows. Reports and live trips are stored in `backend/var/safeher.sqlite3` (gitignored; Firestore later). See §8.13.

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
- **Implemented rules** (`backend/safeher/scoring.py`, `signals.py`). Every value below lives in **`config/scoring.json`** (version `2026-10-13.1`):
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
| Community reports | **0.5, fixed.** It used to rise with the number of reports (0.4 → 0.9). Random end-to-end testing showed that let a *negative* report raise a stretch's score at night, by giving more weight to a value still higher than the other signals. A property test now checks over 5,000 random cases that negative reports never raise a score and positive ones never lower it. |

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
*Superseded by §8.13:* the per-area snapshots in `data/areas/` were removed; all data now comes from `python -m safeher.data build`.

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


### 8.6 Configuration: nothing city- or model-specific in code (13 Oct 2026)
| File | Holds |
|---|---|
| `config/cities.json` | Supported cities: bbox, timezone, OSM extract, landmarks, default trip, OSM → NCRB district mapping; data sources (Geofabrik, NASA product/year, NCRB URL and heads). Adding a city means one entry plus `python -m safeher.data build`. Validated on start-up: landmarks must lie inside their city, and the default trip must name two of its landmarks. |
| `config/scoring.json` | The published, versioned scoring model: weights, route formula, bands, every signal threshold and confidence, report severities, typical opening hours, routing limits, when reasons are shown. Weights must sum to 1 (checked on start-up). |
| `config/app.json` | Default city, trip limits (max km, corridor margin, corridor cache), departure time (`"21:30"` for night demos, `null` for now), helplines, disclaimer, basemaps, languages, nearby/search limits. |
| `backend/.env` / environment | Secrets and machine-specific values: `GEMINI_API_KEY`, `GEMINI_MODEL`, `SAFEHER_LLM`, `OLLAMA_URL`, `OLLAMA_MODEL`, `EARTHDATA_TOKEN`, `OVERPASS_URLS`, `SAFEHER_CONFIG_DIR`, `SAFEHER_URL` (demo recorder). |

- The frontend reads its city data, timezone, map styles, default trip and time, and languages from `/api/cities` and `/api/meta`; `app.js` holds no city data.
- The demo recorder's captions (scores, lamp counts, times) come from the live API at recording time.
- Tests compare against computed results, not fixed numbers.
- The start-up validation found that the "Hauz Khas Village" landmark lay outside the Delhi bbox, so picking it would have failed. It was removed. Enlarging the area (and re-fetching it) would restore it.

### 8.7 Search (implemented: `backend/safeher/search.py`)
- Offline index per area built from the cached snapshot: landmarks, **every named place** in `pois.json` (not only scoring kinds), and **every named street** in the walking graph.
- Matching ignores case and accents: exact name, then name prefix, then every typed word starting a word in the name ("12th main" → "12th Main Road"), then substring. Landmarks rank above places, places above streets.
- Places sharing a name show their nearest street ("Starbucks · café · 100 Feet Road").
- If nothing matches in the current area, matches from the other areas come back flagged; picking one switches city. If nothing matches anywhere, the app says which areas it covers.
- **Not found yet:** abbreviations (OSM stores "State Bank of India", not "SBI"), place types not downloaded (bars, offices), house addresses, and anything outside the demo areas. An open geocoder (Photon / Nominatim) would cover these; it is optional because it sends typed queries to a third party.

### 8.8 Google Maps Platform (implemented, off until a key is set; `backend/safeher/google.py`)
| Feature | API | Behaviour without key / on failure |
|---|---|---|
| Basemap | **Map Tiles API** 2D roadmap, light + dark (styled session), proxied by the backend; Google Maps wordmark + live viewport attribution | CARTO basemap |
| Search | **Places API (New) Autocomplete**, restricted to the current area's rectangle, session tokens; coordinates only via Place Details on selection | Offline search (§8.7) |
| Nearby quick actions | **Places API (New) Nearby Search** with `currentOpeningHours.openNow`, for trips leaving within 30 min of now | OSM with typical hours |
| Route comparison | **Routes API** walking routes with alternatives, **scored by SafeHer's engine** (each piece takes the nearest OSM street's tags); a Google route matching one of ours (≥85% within 20 m) becomes an "Also Google's route" chip | SafeHer routes only, with a data note |
| Street View | **Street View Static API** image of a weak stretch, facing along it; free metadata check first | Button hidden |
| Explanations | **Gemini** (§5.4) | Template |

- **Terms:** Places and Routes content may only appear on a Google map, so every Google content feature also requires the Google basemap (`g_on()` in `api.py`, tested). Only place IDs are kept; tiles and images pass through with `Cache-Control: no-store`. The key never reaches the browser.
- **Config:** `config/app.json → google` (feature switches, place types per quick action, timeouts, dark styles). Key: `GOOGLE_MAPS_API_KEY` in `backend/.env`.
- **Verification:** 23 tests against a mock built from documented response shapes (request building, parsing, fallbacks, terms), plus a test-only simulated-Google server for browser tests. **Real behaviour is checked with `python -m safeher.google_check`**: on 16 Oct 2026 all 8 checks passed with a real key. Tests never call real Google unless `SAFEHER_LIVE_GOOGLE=1`.
- **Live findings (16 Oct 2026):** Google's walking route had outscored SafeHer's "Most support" (80 vs 78), because the support search optimised a per-street average and missed the weakest-stretch term. Now every support setting is tried and candidates are ranked by the full route score. Result: Delhi at night, SafeHer 81 in 37 min vs Google 80 in 41 min; Bengaluru, SafeHer's route *is* Google's route ("Also Google's route"). Google's washroom data (Sulabh/SBM toilets, open now) is far better than OSM's. Its "police" results include some mislabelled places (e.g. a community centre in a police colony), shown as Google lists them.

### 8.9 Stress testing (13–14 Oct 2026)
| Layer | What | Found and fixed |
|---|---|---|
| Random trips | Seeded trips from the street graph, every feature checked; 280+ trips | Negative report raising a score; band/weak-stretch/minute rounding |
| Data integrity | Graph coordinates, edge length vs geometry, connectivity (100% of nodes in one component in both areas), tag vocabularies, places (unique, in area, every quick-action kind present), opening-hours parsing (1 unparseable value in Delhi), help points reachable, landmark snapping (all < 50 m), NCRB rebuild equality, config consistency | Nothing wrong in the data; one test bug |
| Fuzzing (hypothesis) | Random and malformed input to every endpoint incl. live sharing, up to 1,000 cases each; no 5xx allowed | Year-9999 departure overflowed Plan B (→ departure window, `departure_window_days`); report in Halasuru Lake crashed (→ 422) |
| Load (`tests/tools/load_test.py`) | 40 simultaneous users, realistic mix, 60 s | "database is locked" in live sharing; explain re-planned every trip |
| Equivalence | Optimised Plan B vs original on random trips around closing times | Identical output |

**Load results (40 users, same seed):** 1 worker before: 15.8 req/s, plan p50 2.8 s, 8 failures → 4 workers after: **118.6 req/s, 0 errors**, plan p50 0.20 s (p99 5.9 s on cold hours), explain/search/nearby p50 6–12 ms, live sharing p50 0.27–0.59 s under deliberately saturated load.

**Live-sharing storage:** stays SQLite (transactional, many small writes). WAL for multi-worker reads, in-process write lock, and a WAL checkpoint + truncate after any deletion so a stopped/expired location leaves no bytes on disk (tested with a second worker connection open). **DuckDB** was considered and suits the planned analytics dashboard (§5.8), not this store: it allows only one read-write process per database file.

### 8.10 Go anywhere (16 Oct 2026; *removed in §8.13*)
- Trips outside the configured areas are allowed up to `anywhere.max_trip_km` (8 km straight line). The first plan for a new place downloads OpenStreetMap data for the trip's **corridor** (both points + 1.2 km, rounded so nearby trips share it) into `data/areas/trip-<hash>/` (gitignored), checks it (≥ 30 street points, ≥ 90% connected), then plans normally. Later trips inside it are instant.
- While downloading, `/api/plan` returns **202** with the job status and **Google's walking route as an unscored preview** (Google map only). The app shows a progress card and polls `/api/prepare/{id}`. Failures are reported with a retry button; failed downloads aren't retried for 60 s.
- One download per corridor across workers (file lock); status on disk so any worker can answer; departures use the location's own timezone (`timezonefinder`); search is Google-biased to the map centre instead of restricted to demo areas; nearby works anywhere via Google.
- **Measured problem:** public Overpass servers took 343 s for a small Mumbai corridor and over 10 minutes (stalled) for Toit → Koramangala (7.4 × 4.2 km). On-demand downloads are too slow for a demo → plan: pre-load full Delhi and Bengaluru from Geofabrik extracts and cut corridors locally.

### 8.11 Full-city street data (16 Oct 2026; *consolidated into §8.13*)
- **Source:** Geofabrik extracts `northern-zone-latest.osm.pbf` (224 MB, Delhi) and `southern-zone-latest.osm.pbf` (544 MB, Bengaluru), data to 15 Oct 2026, checksums verified, kept in `data/raw/osm/` (gitignored).
- **Build** (`python -m safeher.citydata build`, about 1 minute per city): one pyosmium pass keeps walkable street segments (OSMnx "walk" rules) and the same place categories as the Overpass download, inside each city's bbox, stored in **DuckDB** (`data/cities/<id>.duckdb`, gitignored), sorted by location.
  - Delhi: 997,981 segments, 14,581 places, 36 MB. Bengaluru: 612,470 segments, 24,455 places, 24 MB.
- **Use:** a trip corridor inside a built city is cut from DuckDB (opened read-only, so all workers share it), simplified with OSMnx and saved in the normal area format. **Toit → Koramangala: ready in 2.5 s** (was > 10 min via Overpass). Route lengths match the Overpass-built network exactly (2,955 / 3,200 / 2,838 m for Hauz Khas → Saket). Outside the two cities, the on-demand Overpass download still applies.
- **Known gap:** trip corridors don't map to an NCRB district, so their incidents signal is "no data" (weight redistributed).

### 8.12 NASA night lights (live since 16 Oct 2026)
- Black Marble **VNP46A4 2025** (yearly, ~500 m) downloaded with the Earthdata token after accepting the LAADS data licence once in a browser (the token alone was redirected to the licence page).
- Grids: the two demo areas (`data/areas/<id>/viirs.npz`, committed) and **whole cities** (`data/cities/<id>_viirs.npz`, gitignored; `python -m safeher.viirs cities`): Delhi 125×118 px, Bengaluru 79×77 px. Trip corridors use their city's grid, so brightness is ranked city-wide.
- Checks: Hauz Khas Deer Park (radiance 38.5, 11th percentile) is darker than Aurobindo Marg at Hauz Khas Metro (54.6, 52nd) and Select Citywalk (75.7, 97th). Jahanpanah City Forest reads 65th percentile: 500 m pixels blend a forest edge with nearby roads, which is why VIIRS never overrides street-level data.
- Effect at 9:30 pm: Delhi demo trip 81/70/68 → 84/75/73 (lighting confidence 0.27 → 0.45); Bengaluru 93/89/74 → 94/93/75. Ranking unchanged.

### 8.13 One database, supported cities only (16 Oct 2026: `safeher/data.py`, `store.py`, `cities.py`, `userdb.py`)
Data used to live in five places (committed area snapshots, per-city DuckDB files, NumPy grids, an NCRB JSON and a JSONL of reports). It is now two files:

| File | Holds | Written by |
|---|---|---|
| `data/safeher.duckdb` (~145 MB, gitignored) | Reference data for every city: `segments` (walkable streets), `places`, `night_lights` (NASA pixels), `districts` (OSM admin_level-5 polygons), `incidents` (NCRB per police district), `sources` (what was built from what, and when) | `python -m safeher.data build`, the only build command; opened **read-only** by every worker |
| `backend/var/safeher.sqlite3` (gitignored, chmod 600) | User data: community reports and live shared trips | The API (WAL, `secure_delete`, checkpoint after deletes so removed locations don't linger) |

- **Supported cities only.** Delhi and Bengaluru, whole-city (Delhi 998k street segments, 33k places; Bengaluru 612k, 51k). A trip outside them, across two cities, or longer than `max_trip_km` (8 km) gets a 422 with a message the user can act on. The on-demand download queue (§8.10) and Google preview were removed: nothing is fetched while planning.
- **Corridors in memory.** A trip is planned on its corridor (both points + 1.2 km, rounded to 0.001° so nearby trips share one), built from DuckDB in 0.2–3 s and kept in an LRU of 24. Nothing is written to disk.
- **Incidents per point.** NCRB figures are no longer assigned per area: each point on a route is looked up in OSM district polygons (shapely, prepared) and mapped to its NCRB police district via `config/cities.json`. A route crossing districts uses each one's figure, and the data note names them (e.g. Connaught Place → Karol Bagh: New Delhi and Central). Old Delhi and Central North Delhi (2023 revenue districts) and the Gurugram/Faridabad parts of the Delhi bbox have no mapping → "no data", weight redistributed. Bengaluru has no NCRB district split → "no data".
- **Search** covers the whole city: landmarks, every named place (now incl. bars, offices, transit), neighbourhoods (`place=suburb` etc.) and streets (one entry per name per ~2 km). Results show their neighbourhood; equal matches are ordered by distance from the map centre. Google suggestions are restricted to the current city's rectangle and are only de-duplicated against that city's results.
- **Map layers** are loaded for the visible box at zoom ≥ 14 (`/api/layers?bbox`); a whole-city request is refused. At night the map shows three light layers: **NASA night-light pixels** (~460 m squares, glowing only where brighter than the city median, so dark parks and lakes stand out), **streets tagged `lit=yes`** in OpenStreetMap, and **mapped street lamps**. Lamps alone looked sparse because few are mapped (Bengaluru 6,004, Delhi 1,523; Hauz Khas–Saket has 1, but 106 lit-street segments and 63 NASA pixels). NASA data shows lit *areas*; it can't place individual lamps.
- **Strict:** if the database is missing the API fails with the build command, never with an empty map.

## 9. Architecture (Google stack)

*What is built today is in [ARCHITECTURE.md](ARCHITECTURE.md). This section is the target design on Google Cloud.*

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
| 13 Oct 2026 | All city data, model parameters and app settings moved to `config/*.json`; secrets and machine values via environment (§8.6) | "Make sure nothing is hardcoded": new cities and model changes need no code edits; published weights live in one versioned file |
| 13 Oct 2026 | Reports signal confidence fixed at 0.5 (§7.3) | Random testing found a negative report could raise a score |
| 13 Oct 2026 | Band, weak stretches and walking time computed from the displayed (rounded) numbers | Random testing found "50 · Less support", "weak stretch · score 50" and minute values off by one |
| 13 Oct 2026 | Random end-to-end tests: trips drawn from the street graph with a printed seed, every feature checked, plus 14 invalid-request cases (`tests/test_random_trips.py`) | Fixed demo routes never hit boundary cases; 280+ random trips now pass |
| 13 Oct 2026 | Offline place + street search with disambiguation and cross-city results (§8.7) | Typing a street or a chain name should work like a maps app |
| 14 Oct 2026 | Merged Anushka's live sharing and Plan B coverage (branch `main-anushka`) into `feature/google-integration` | Teammate work; all her tests pass |
| 14 Oct 2026 | Plan B made 5–6× faster with identical output; its constants moved to config | Plan latency had grown to 0.5–1.7 s per request |
| 14 Oct 2026 | Google Maps Platform as optional layer with Google basemap gating all Google content (§8.8) | Google's terms; demo never depends on a key |
| 14 Oct 2026 | Stress-testing suite: data integrity, fuzzing, load, equivalence (§8.9) | "Stress test all the data" |
| 14 Oct 2026 | Run with 4 workers; plans cached and shared with explanations | 7× throughput under load |
| 14 Oct 2026 | Live sharing stays on SQLite (WAL + wipe after delete); DuckDB reserved for analytics | DuckDB is single-writer-process; SQLite fits small concurrent writes |
| 16 Oct 2026 | "Most support" = best full route score among all support settings (λ 6, 3, 1.5, 0.75); config v2026-10-16.1 | Real Google comparison exposed a better route the old search missed |
| 16 Oct 2026 | Compare only Google's recommended route (`google.max_routes = 1`); keep Google places whose primary type was requested | Six route cards were noise; type filter removes obvious mismatches |
| 16 Oct 2026 | Tests never call real Google by default | No cost, no network dependence, reproducible results |
| 16 Oct 2026 | Search ignores apostrophes; Google session tokens validated (URL-safe base64 ≤ 36) and Google errors logged, never silently dropped | Real query "bobs bar" returned nothing: apostrophe mismatch locally and a rejected token hidden by a silent fallback |
| 16 Oct 2026 | OpenStreetMap stays as the street network and scoring data; Google adds search, live hours, basemap and route comparison | Google provides no lighting, lamps or walkable street graph, and its terms forbid storing its data to build our own scores |
| 16 Oct 2026 | Go anywhere: corridor downloads on first use + Google preview meanwhile (§8.10) | User: "go anywhere, not just demo areas"; Google can route but can't supply scoring data |
| 16 Oct 2026 | Tests never download street data (trip areas go to a temp folder, download stubbed) | A test downloaded a real Mumbai corridor during the run |
| 16 Oct 2026 | **No silent fallbacks.** A configured key that fails (Gemini, Google Maps), or a Gemini answer that fails the fact checks twice, is an error shown to the user (HTTP 502 + message). A key that isn't set means the feature is off. Retries for "busy" (429/503) are kept: retrying isn't falling back | User: "I want it to fail if any token is not working or anything is wrong" |
| 16 Oct 2026 | Gemini model and thinking level in `config/app.json` (`gemini-3.8-flash`, `thinkingLevel: low`) | `gemini-2.5-flash` is closed to new users; newer Flash models spent the whole output allowance thinking and returned no text |
| 16 Oct 2026 | Tests hide every real key (Maps, Gemini, Earthdata) unless `SAFEHER_LIVE_GOOGLE=1` | A test had reached the real Gemini key |
| 16 Oct 2026 | Pre-load full Delhi and Bengaluru from Geofabrik into DuckDB; cut trip corridors locally (§8.11) | Overpass on demand took 6–10+ min; DuckDB suits large read-only city tables shared by all workers |
| 16 Oct 2026 | Place-name guardrail ignores names inside places that are in the facts, and matches whole words | Real Gemini answer naming "Indiranagar Traffic Police Station" was rejected because another place is called "Indiranagar" |
| 16 Oct 2026 | Status files written via unique temp names | Shared temp file caused spurious "download failed" under concurrency |
| 16 Oct 2026 | NASA night lights live for demo areas and whole cities; trip corridors use the city grid (§8.12) | Delhi lighting was almost entirely a road-type guess |
| 16 Oct 2026 | Consolidate all reference data into one DuckDB (`python -m safeher.data build`, not committed) and all user data into one SQLite (§8.13) | Data was spread over five formats and folders; one build command, one read-only file shared by every worker |
| 16 Oct 2026 | Supported cities only; trips elsewhere get a clear 422; download queue removed (§8.13) | User choice: predictable, no multi-minute waits mid-demo |
| 16 Oct 2026 | NCRB incidents looked up per point via OSM district polygons (§8.13) | Corridors can cross police districts; one value per area was wrong for city-wide trips |
| 16 Oct 2026 | Layers by visible bbox at zoom ≥ 14; search city-wide with neighbourhoods and nearest-first | Whole-city layers would be hundreds of thousands of points |
| 16 Oct 2026 | Night map shows NASA night-light glow and OSM lit streets alongside mapped lamps (§8.13) | User: lamp dots were very sparse in Bengaluru; few lamps are mapped in OSM |
| 16 Oct 2026 | Enter in a search box searches the typed text first; late search replies are dropped; browser tests (`tests/test_web_ui.py`, Playwright) | User: changing start/end didn't refresh. Enter picked the stale landmark list shown on focus |
| 16 Oct 2026 | Architecture diagrams live in the repo (`docs/ARCHITECTURE.md`, SVGs generated by `docs/architecture/build.py`); README gets a quickstart and `backend/.env.example` | Teammates need to set up keys and data without asking |
