# Record the latest SafeHer demo

`record_demo.py` is the only recorder. It records the current UI from scratch—no previous video is needed—and publishes:

- `docs/media/safeher-demo-herside.mp4`
- `docs/media/safeher-demo-herside-poster.jpg`

## Requirements and command

Start SafeHer, Ollama with the tool-capable `llama3.2:3b` model, and working Google Places credentials. Install Chrome and system ffmpeg. Use the repository virtual environment (Playwright and NetworkX are required).

```bash
# From the repository root:
mkdir -p demo/out/assets
curl -fL https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.js -o demo/out/assets/maplibre.js
curl -fL https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.css -o demo/out/assets/maplibre.css
.venv/bin/python demo/record_demo.py --base http://localhost:8767
```

Add `--headed` to watch. If downloads require the corporate proxy, prefix curl with `HTTP_PROXY=http://sysproxy.wal-mart.com:8080 HTTPS_PROXY=http://sysproxy.wal-mart.com:8080`.

## Walkthrough and authenticity

The silent, captioned video covers routes/time-dependent evidence, Plan B route comparisons and gaps, opt-in live sharing including recipient movement/revocation, then HerSide Q&A and model-independent SOS.

The recorded question is:

> Find the nearest pharmacy to these confirmed coordinates: 28.5433, 77.2067.

This worked in three consecutive live Ollama checks before recording. Explicit coordinates avoid the remaining location-resolution ambiguity of the small model. Search responses are real: the recorder asserts the `local_enquiry` intent and actual `google_places` records, and refuses to publish a failed/fallback chat. It does not fake replies or hide failures through response interception. Future model/provider responses can vary.

Only map assets are intercepted: cached MapLibre and an OSM walking-graph street map avoid external tile outages. Sharing GPS is Playwright-simulated and visibly labelled, never a real person's position. Sharing sessions are revoked in cleanup. No reports are submitted, no calls are placed, and no Google results are plotted on the OSM basemap.

Temporary video is removed automatically. The MP4 is encoded and fully decoded for validation before replacing the published video; failed runs leave the previous output untouched. The new poster comes from the actual HerSide results screen. Keep only this recorder/video/poster; previous versions are available in Git history.
