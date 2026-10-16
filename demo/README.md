# Record the latest SafeHer demo

`record_demo.py` is the only recorder. It records the current UI from scratch—no previous video is needed—and publishes:

- `docs/media/safeher-demo-herside.mp4`
- `docs/media/safeher-demo-herside-poster.jpg`

## Requirements and command

Start SafeHer, Ollama with the tool-capable `llama3.2:3b` model, (Google Places credentials are only needed for optional nearby-place tools). Install Chrome and system ffmpeg. Use the repository virtual environment (Playwright and NetworkX are required).

```bash
# From the repository root:
mkdir -p demo/out/assets
curl -fL https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.js -o demo/out/assets/maplibre.js
curl -fL https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.css -o demo/out/assets/maplibre.css
backend/.venv/bin/python demo/record_demo.py --base http://localhost:8765
```

Add `--headed` to watch. Needs the data built first (`python -m safeher.data build`): the cached street map is drawn from each city's default-trip corridor in `data/safeher.duckdb`. If downloads need a proxy, set `HTTP_PROXY`/`HTTPS_PROXY` for curl.

## Walkthrough and authenticity

The silent, captioned video covers routes/time-dependent evidence, Plan B route comparisons and gaps, opt-in live sharing including recipient movement/revocation, then HerSide Q&A and model-independent SOS.

The recorded question is:

> For this walk, is the extra walking time worth it for better lighting and fewer weak stretches?

The recorder clicks **Compare my selected trip** to attach origin, destination and departure as structured context; the user reviews a normal question, place labels and departure, not coordinate text. Ollama/ADK selects the pre-trip comparison tool. The backend recomputes the real plan and renders the measured extra minutes, support/lighting scores, confidence and weak-stretch counts. No model-generated facts or route fixtures are substituted. This question passed three consecutive live Ollama checks before recording; future responses can vary.

Publication requires a real pre-trip response with at least two routes and the measured trade-off. Failed runs do not replace the previous video. The comparison is explicitly not a guarantee of safety.

Only map assets are intercepted: cached MapLibre and an OSM walking-graph street map avoid external tile outages. Sharing GPS is Playwright-simulated and visibly labelled, never a real person's position. Sharing sessions are revoked in cleanup. No reports are submitted, no calls are placed, and no Google results are plotted on the OSM basemap.

Temporary video is removed automatically. The MP4 is encoded and fully decoded for validation before replacing the published video; failed runs leave the previous output untouched. The new poster comes from the actual HerSide results screen. Keep only this recorder/video/poster; previous versions are available in Git history.
