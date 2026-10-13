# SafeHer

**AI-powered women's safety and mobility platform** — built for the Google AI Builder Cup (Sustainability & Social Welfare).

SafeHer compares routes not just by time and distance but by **street lighting, transit access, nearby safe spaces, historical incidents and recent community reports**. Gemini explains in plain language — in your language — why one route may be preferable. Find the nearest police station, washroom or well-lit place in two taps, report hazards, and share your trip with people you trust.

> SafeHer helps you make safer, better-informed decisions. It does not guarantee safety. In an emergency, call **112**.

## Demo

[![SafeHer demo: Hauz Khas Metro to Select Citywalk, Delhi, at 9:30 PM. Click to play the video.](docs/media/demo-poster.jpg)](docs/media/safeher-demo.mp4)

▶️ **[Watch the 2½-minute demo video](docs/media/safeher-demo.mp4)** (MP4, 8.5 MB, captioned). It shows:
- Night-time route comparison in Delhi: the best-supported route is 1 min longer and scores 77 vs 67
- Weak stretches, safe stops and the per-signal breakdown
- Scores changing with time of day (1 PM vs 11:45 PM)
- Nearest police and washrooms, and how the score is calculated
- Bengaluru's street lamps on the night map, reporting and SOS

## Updated demo · live location sharing

[![Updated SafeHer demo with live location sharing.](docs/media/safeher-demo-live-sharing-poster.jpg)](docs/media/safeher-demo-live-sharing.mp4)

▶ **[Watch the updated demo](docs/media/safeher-demo-live-sharing.mp4)** — the original walkthrough plus a new live-sharing chapter: opt-in GPS sharing, recipient map, simulated movement, accuracy/update age, foreground limitations, and stopping/revoking the link. The new chapter is labelled **simulated GPS / cached OSM map**. The original video and poster above remain unchanged.

Recording instructions: [demo/README.md](demo/README.md). The new recorder never overwrites either version.

## MVP features
1. Map with 2–3 candidate routes
2. Transparent, rule-based safety score
3. Five data signals: lighting · transit proximity · verified safe spaces · historical anonymised incidents · recent community reports
4. Gemini route explanation and multilingual Q&A
5. Safe stops: nearest police station, closest washroom, well-lit places along the route
6. Community hazard reporting
7. Opt-in live location sharing (web foreground-only; expiring viewer link)
8. Aggregated, privacy-preserving hotspot dashboard

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
With the server running from `backend/`: `../.venv/bin/python -m playwright install chromium` (once), then `../.venv/bin/python ../demo/record_demo.py` → `demo/out/safeher-demo.mp4` (≈2½ min, captioned). Add `--headed` to watch it record.

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
