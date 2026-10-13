# Demo recordings

The original `record_demo.py`, `docs/media/safeher-demo.mp4`, and its poster are preserved.

## Latest demo: Plan B Coverage + live sharing

`record_demo_plan_b.py` appends a newly recorded Plan B chapter to the live-sharing demo as a separate third version:

- `docs/media/safeher-demo-plan-b-and-live-sharing.mp4`
- `docs/media/safeher-demo-plan-b-and-live-sharing-poster.jpg`

It shows real API-derived coverage estimates, changes departure to 11:45 PM, compares Shortest and Alternative routes, highlights gaps, focuses the least-covered sampled point, and explains uncertainty. No GPS access or new sharing sessions are needed for this chapter. The cached-map approach and requirements below also apply. Earlier videos and recorders are not modified.

```bash
# From repo root, with the server running and cached MapLibre assets available:
.venv/bin/python demo/record_demo_plan_b.py
```

Add `--headed` to watch. Existing outputs are never overwritten; use a new `NAME` for future revisions.

## Updated demo: live sharing

`record_demo_live.py` records a new live-sharing chapter and appends it to the original footage in a **new** file:

- `docs/media/safeher-demo-live-sharing.mp4`
- `docs/media/safeher-demo-live-sharing-poster.jpg`

The chapter demonstrates opt-in sharing, the recipient map, simulated movement, foreground limitations, and revocation. GPS is Playwright-simulated; no actual device location is used. Created sessions are revoked in cleanup. No reports are submitted. Captions are burned into the silent video, matching the original format.

The new chapter uses a clearly labelled cached OpenStreetMap street map from the checked-in Delhi walking graph, because external basemaps can be blocked on corporate networks. This is recording-only request interception, not a change to the app. MapLibre assets are cached in ignored `demo/out/assets/`; fonts use system fallbacks. The app’s real API and GPS-watch workflow remain in use.

From the repository root, with SafeHer running on localhost:8765:

```bash
# Requires project Playwright, Google Chrome, and system ffmpeg.
.venv/bin/python -m playwright install ffmpeg
mkdir -p demo/out/assets
curl -fL https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.js -o demo/out/assets/maplibre.js
curl -fL https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.css -o demo/out/assets/maplibre.css
.venv/bin/python demo/record_demo_live.py
```

If downloads need the corporate proxy, prepend `HTTP_PROXY=http://sysproxy.wal-mart.com:8080 HTTPS_PROXY=http://sysproxy.wal-mart.com:8080` to the download command. Add `--headed` to watch recording.

The recorder refuses to overwrite existing updated outputs. To record another revision, change `NAME` to a new filename. Temporary raw footage is removed automatically; the old published MP4 is read-only input to concatenation.
