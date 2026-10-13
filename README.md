# SafeHer

**AI-powered women's safety and mobility platform** — built for the Google AI Builder Cup (Sustainability & Social Welfare).

SafeHer compares routes not just by time and distance but by **street lighting, transit access, nearby safe spaces, historical incidents and recent community reports**. Gemini explains in plain language — in your language — why one route may be preferable. Find the nearest police station, washroom or well-lit place in two taps, report hazards, and share your trip with people you trust.

> SafeHer helps you make safer, better-informed decisions. It does not guarantee safety. In an emergency, call **112**.

## Demo

[![SafeHer demo: Hauz Khas Metro to Select Citywalk, Delhi, at 9:30 PM. Click to play the video.](docs/media/demo-poster.jpg)](docs/media/safeher-demo.mp4)

▶️ **[Watch the 2½-minute demo video](docs/media/safeher-demo.mp4)** (MP4, 8.7 MB, captioned). It shows:
- Night-time route comparison in Delhi: the best-supported route is slightly longer and scores higher than the shortest
- Weak stretches, safe stops and the per-signal breakdown
- Scores changing with time of day (1 PM vs 11:45 PM)
- Nearest police and washrooms, and how the score is calculated
- Bengaluru's street lamps on the night map, reporting and SOS

## MVP features
1. Map with 2–3 candidate routes
2. Transparent, rule-based safety score
3. Five data signals: lighting · transit proximity · verified safe spaces · historical anonymised incidents · recent community reports
4. Gemini route explanation and multilingual Q&A
5. Safe stops: nearest police station, closest washroom, well-lit places along the route
6. Community hazard reporting
7. Trusted-contact trip sharing
8. Aggregated, privacy-preserving hotspot dashboard

## Run the demo
```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn safeher.api:app --port 8765
```
Open http://localhost:8765.

Settings live in `config/`: demo areas and landmarks (`areas.json`), the published scoring model (`scoring.json`) and app defaults such as the opening time and helplines (`app.json`). Add an area by adding an entry to `areas.json` and running `.venv/bin/python -m safeher.fetch <area-id>`.

Route explanations use **Gemini** when `GEMINI_API_KEY=...` is set in `backend/.env` (free key from [Google AI Studio](https://aistudio.google.com/apikey); never commit it). Without a key, a fact-checked template is used. Map data for both demo areas is already in `data/areas/`; rebuild it with `.venv/bin/python -m safeher.fetch`. Run the tests with `.venv/bin/pytest`.

### Record the demo video
With the server running: `backend/.venv/bin/python -m playwright install chromium` (once), then `backend/.venv/bin/python demo/record_demo.py` → `demo/out/safeher-demo.mp4` (≈2½ min, captioned). Add `--headed` to watch it record.

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
