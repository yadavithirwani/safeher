# SafeHer

**AI-powered women's safety and mobility platform** — built for the Google AI Builder Cup (Sustainability & Social Welfare).

SafeHer compares routes not just by time and distance but by **street lighting, transit access, nearby safe spaces, historical incidents and recent community reports**. Gemini explains in plain language — in your language — why one route may be preferable. Find the nearest police station, washroom or well-lit place in two taps, report hazards, and share your trip with people you trust.

> SafeHer helps you make safer, better-informed decisions. It does not guarantee safety. In an emergency, call **112**.

## Current implementation guide

**[Read the end-to-end pipeline, inputs, outputs and feature status](docs/CURRENT_PIPELINE.md)** — the source of truth for what is implemented today, including the exact AI boundary, datasets, scoring, Plan B Coverage, live sharing, API contracts and limitations. [The design spec](docs/DESIGN_SPEC.md) also includes planned features; it is not an implementation checklist.

## HerSide · chat companion

Initial Google ADK supervisor/specialist agents and `POST /api/chat` are implemented, with Ollama configuration, Google Maps tools and a model-independent SOS response. **Live Ollama/ADK/Places smoke checks have passed; model reliability and Geocoding configuration still need work.** Setup, tests and limitations: [backend/CHATBOT_README.md](backend/CHATBOT_README.md). Open **HerSide · Ask for help** above the route cards for nearby-place questions, selected-trip comparisons, one-shot location attachment and SOS call options. Chat context stays in memory, not browser storage; clearing the UI does not immediately delete the expiring server context. Frontend integration is an initial version, not a production emergency service.

## Latest demo · HerSide Q&A + Plan B + live sharing

[![SafeHer with HerSide pre-trip comparison of walking time, lighting evidence and route support.](docs/media/safeher-demo-herside-poster.jpg)](docs/media/safeher-demo-herside.mp4)

**[Watch the latest captioned demo](docs/media/safeher-demo-herside.mp4)** — 4:54, 1440×900, approximately 41 MB. It covers:
- Delhi walking-route comparison, transparent signals and changing departure times
- Plan B estimates, coverage gaps and uncertainty
- Opt-in live sharing, recipient view, simulated movement and revoking the link
- **HerSide pre-trip Q&A powered by local Ollama**: whether extra walking time is worth better lighting evidence and fewer weak stretches
- Model-independent SOS and explicit, one-shot chatbot location consent

The pre-trip question uses a reviewed structured trip attachment, not user-typed coordinates; computed route results are not mocked. Sharing GPS is simulated and labelled. The recording uses a labelled cached OSM map. Only the latest recorder, video and poster are kept; earlier versions remain in Git history.

Recording instructions: [demo/README.md](demo/README.md).

## Plan B Coverage

Route cards now show a separate **Plan B Coverage** estimate: the longest sampled walk to a potentially available help point, route share within five minutes, and clickable coverage gaps. It does not change safety scores or route ranking.

- Uses directed shortest walking-network distances to police, hospitals, pharmacies, shops and other existing support POIs; explicitly restricted-access places are excluded.
- Samples roughly every 50 m. Opening hours are checked at estimated route arrival time in minute buckets, conservatively requiring the place to remain open through the following 16-minute window. This can exclude a nearby place that closes soon.
- Missing/invalid opening hours use flagged typical-hour assumptions; mapped hours do not verify real availability or willingness to assist.
- A point-to-street connector within 100 m is a straight-line approximation, not a verified entrance or crossing. Interior edge access and travel time are estimates, not navigation instructions.
- Searches up to 15 minutes at 80 m/min. Missing or disconnected mapped coverage is shown as unknown, never zero or evidence that no help exists. Graph boundaries and incomplete POI data limit results.
- The UI lists gaps beyond five minutes and can highlight them on the map. This is not a guarantee of refuge or emergency assistance.

Tests: `cd backend && ../.venv/bin/pytest tests/test_backup.py`.

## MVP features
1. Map with 2–3 candidate routes
2. Transparent, rule-based safety score
3. Five data signals: lighting · transit proximity · mapped potentially open places · district-level historical incidents · recent community reports
4. English/Hindi route explanations via optional Gemini or Ollama, with a template fallback
5. Stops along routes and nearby police stations, washrooms, hospitals and pharmacies
6. Community hazard reporting (pending reports; no moderation/verification workflow yet)
7. Opt-in live location sharing (web foreground-only; expiring viewer link)
8. Plan B walking-network coverage estimates and clickable gaps

General multilingual Q&A and the aggregated H3 hotspot dashboard are **planned, not implemented**.

## Run the demo
```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e './backend[dev]' --index-url https://pypi.ci.artifacts.walmart.com/artifactory/api/pypi/external-pypi/simple --allow-insecure-host pypi.ci.artifacts.walmart.com
cd backend
../.venv/bin/uvicorn safeher.api:app --port 8765 --no-access-log
```
Open http://localhost:8765.

Route explanations use **Gemini** when `GEMINI_API_KEY=...` is set in `backend/.env` (free key from [Google AI Studio](https://aistudio.google.com/apikey); never commit it). Without a key, a fact-checked template is used. Map data for both demo areas is already in `data/areas/`; rebuild it with `../.venv/bin/python -m safeher.fetch`. Run the tests with `../.venv/bin/pytest`.

### Record the demo video
With SafeHer and Ollama running, from the repository root: `.venv/bin/python demo/record_demo.py --base http://localhost:8767` → `docs/media/safeher-demo-herside.mp4`. Requires Chrome, ffmpeg and cached MapLibre assets; see [demo/README.md](demo/README.md). Add `--headed` to watch.

## Live location sharing (foreground MVP)

Click **Share live location → Start sharing**, grant browser location permission, and copy the viewer link to a trusted contact. The viewer opens a standalone map without installing an app. Only the latest GPS point, its accuracy, server receipt time, and the route selected at session creation are stored. No location history or LLM processing is involved.

- **Keep the sender page open.** Background tabs and locked phones can pause GPS/network activity. This is not native background tracking.
- Viewer polls every 5 seconds and marks updates older than 30 seconds **STALE**. It shows GPS accuracy, not guaranteed precision or a live ETA.
- Sessions expire after **two hours**. **Stop sharing** clears GPS tracking and deletes the session. If revocation fails offline, the UI warns and lets you retry; the server retains the last point until expiry.
- Reload recovery stores owner credentials in the sender tab’s session storage; resuming GPS requires an explicit click. Closing the tab does not immediately revoke a link. Stop before closing.
- Separate random owner/viewer credentials; only hashed credentials are stored in SQLite (`backend/var/trips.sqlite3`). Anyone holding a viewer link can view it. Do not publish links or log request paths. Use `--no-access-log`; production proxies/APM must also redact `/api/trips/*` paths and request bodies.
- Expired data is purged at most a minute after expiry while the server runs, and on subsequent database access. Stopped data is deleted immediately; secure deletion is enabled. Database backups or recipient screenshots are outside this prototype’s deletion control.
- GPS works on **localhost or HTTPS**. A localhost link works only on this computer: phone/contact testing needs a reachable HTTPS deployment. Do not use an HTTP LAN IP for GPS.
- Public deployment still needs per-client abuse/rate controls, HTTPS, security review, and device testing. The prototype caps active sessions at 1,000 but is not production-hardened.
- No automatic arrival detection, trusted-contact delivery, SMS, or background-location support yet. Map rendering uses external basemap/CDN services; live GPS is sent only to the SafeHer backend, but map tile providers can infer the viewed area.

Run isolated API regressions from `backend/`: `../.venv/bin/pytest tests/test_trips.py`.

### Local Ollama explanations

Ollama is supported independently of sharing. With Ollama running locally and a model installed:

```bash
# Already installed on the development machine: llama3:latest
ollama list
SAFEHER_LLM=ollama OLLAMA_MODEL=llama3:latest ../.venv/bin/uvicorn safeher.api:app --port 8765 --no-access-log
```

Alternatively set `SAFEHER_LLM=ollama` and `OLLAMA_MODEL=llama3:latest` in the ignored `backend/.env`. Explanations are validated; rejected/unavailable model answers fall back to the deterministic template. No Gemini calls are made in Ollama mode. Model prompts include planned-route facts, not live GPS updates.

## Docs
- [Design spec](docs/DESIGN_SPEC.md)

## Planned stack
Flutter · Google Maps Platform (Maps SDK, Routes, Places) · Gemini API / Vertex AI · Firebase (Auth, Firestore, FCM) · Cloud Run · BigQuery GIS

## Repo layout
```
app/web/    demo web app (MapLibre, served by the API)
dashboard/  community / authority dashboard (web)
backend/    Cloud Run API (scoring, Gemini, reports, trips)
data/       ingestion scripts and pilot-city datasets
docs/       design spec and decisions
```

## Team
Built by the SafeHer team for the Google AI Builder Cup 2026.
