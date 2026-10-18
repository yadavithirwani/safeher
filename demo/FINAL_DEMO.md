# Current demo: Reached?

- Source story: `docs/DEMO_STORYLINE.md`.
- Current output: `docs/media/safeher-final-demo.mp4`.
- Captions: `docs/media/safeher-final-demo.srt`.
- Target/measured runtime: 174 seconds / approximately 174.021 seconds.
- Only the latest narrated cut is retained on `main-anushka-demo`. Earlier tracked cuts remain available on `main-anushka` and in Git history.
- Voice: female Indian-English `en-IN-NeerjaNeural` via edge-tts, regenerated at normal speed (`+0%`). Every scene has at least 0.7 seconds of room after narration; the editor rejects slots that would require acceleration. The revised script uses complete sentences and a connected Meera/Amma story instead of fragments or spoken metric lists. ElevenLabs API access was not configured; no ElevenLabs account or subscription was created.

## Capture and integrity

User approved up to three live recording runs. Two setup attempts failed before usable scene capture; the third completed. No additional paid capture runs were launched. Temporary sharing access was revoked (GET returned 404); no calls were placed or reports submitted.

This review cut uses verified real UI stills and editorial metric panels, not continuous screen recording. The opening/closing message cards are storytelling graphics. Google Maps grounding is shown as an explicitly labeled transcript of the actual captured response: the browser screenshot had scrolled to unrelated tool-result cards and was deliberately not presented as grounding evidence. Displayed source names are a selection of response citations.

Sharing uses simulated GPS near the actual route with successful live location PUT/GET. It is presented as status-only; a rendered contact basemap or moving dot is not claimed. This differs from the draft's requested moving-map scene.

## Reconciled numbers

The deployed app supports departure time but not historical date selection. Captured plans use 18 October 2026, not the draft's 17 October snapshot.

| Metric | Shortest, 10 PM | Most support, 10 PM | Shortest, 11:30 PM | Most support, 11:30 PM |
|---|---|---|---|---|
| Minutes | 13 | 15 | 13 | 13 |
| Score | 67 | 89 | 46 | 58 |
| Mapped lighting | 12.7% | 61.2% | 12.7% | 44.7% |
| Likely-open places | 11 | 34 | 4 | 6 |
| Weak stretch | none | none | 653 m | 133 m |
| Maximum Plan B walk | 5.4 min | 2.1 min | 5.4 min | 3.5 min |

The app rounds some distances and minutes upward in prose. Editorial panels distinguish exact metrics and selected-route versus shortest-route data.

The opening card's city-wide context uses the same combined category as the app: **1,195 molestation and insulting-modesty cases in 2023 versus 592 in 2021**. The draft's 1,135/573 narrower molestation-only figures were not substituted into the combined-category context. These figures are not street-level scoring inputs.

## Build

```bash
uv run --no-sync python demo/build_demo.py
```

Requires the retained ignored capture `demo/out/updated/capture-20261018-150520/manifest.json`, images, ffmpeg, Pillow and edge-tts. The editor reuses shared final-edit utilities with the current capture workspace and publication path.

Before hackathon submission: watch the full updated cut and approve its still-frame presentation and disclosed status-only sharing scene. Full decode, duration and sampled visual checks do not replace a human playback/listening review.
