# SafeHer

**AI-powered women's safety and mobility platform** — built for the Google AI Builder Cup (Sustainability & Social Welfare).

SafeHer compares routes not just by time and distance but by **street lighting, transit access, nearby safe spaces, historical incidents and recent community reports**. Gemini explains in plain language — in your language — why one route may be preferable. Find the nearest police station, washroom or well-lit place in two taps, report hazards, and share your trip with people you trust.

> SafeHer helps you make safer, better-informed decisions. It does not guarantee safety. In an emergency, call **112**.

## MVP features
1. Map with 2–3 candidate routes
2. Transparent, rule-based safety score
3. Five data signals: lighting · transit proximity · verified safe spaces · historical anonymised incidents · recent community reports
4. Gemini route explanation and multilingual Q&A
5. Safe stops: nearest police station, closest washroom, well-lit places along the route
6. Community hazard reporting
7. Trusted-contact trip sharing
8. Aggregated, privacy-preserving hotspot dashboard

## Docs
- [Design spec](docs/DESIGN_SPEC.md)

## Planned stack
Flutter · Google Maps Platform (Maps SDK, Routes, Places) · Gemini API / Vertex AI · Firebase (Auth, Firestore, FCM) · Cloud Run · BigQuery GIS

## Repo layout
```
app/        mobile app (Flutter)
dashboard/  community / authority dashboard (web)
backend/    Cloud Run API (scoring, Gemini, reports, trips)
data/       ingestion scripts and pilot-city datasets
docs/       design spec and decisions
```

## Team
Built by the SafeHer team for the Google AI Builder Cup 2026.
