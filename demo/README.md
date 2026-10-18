# SafeHer demo

## Current review cut

- [Watch the 2:50 demo](../docs/media/safeher-final-demo.mp4)
- [English captions](../docs/media/safeher-final-demo.srt)
- [Story, timings and known limitations](FINAL_DEMO.md)

1920×1080 H.264, female Indian-English neural narration, real Gemini/ADK responses and Maps citations. Sharing uses visibly labeled simulated GPS. The contact viewer is status-only because its basemap was unavailable on the recording network; this limitation is disclosed in the video.

## Current source files

| File | Purpose |
|---|---|
| `final_story.json` | Narration and 170-second scene budget |
| `final_capture.py` | Primary deployed-app scene recorder |
| `final_support_capture.py` | Sharing and SOS capture, with API/revocation checks |
| `final_support_edit.py` | Merge verified support footage into the edit manifest |
| `final_edit.py` | Neural narration, composition, captions and duration validation |

Requires the project virtual environment, Playwright, Chrome, ffmpeg/ffprobe, edge-tts and Pillow. Temporary files and the current editable capture assets live in ignored `demo/out/final/`.

To re-edit the retained capture:

```bash
uv run --no-sync python demo/final_support_edit.py
uv run --no-sync python demo/final_edit.py
```

The editor writes `demo/out/final/safeher-final-demo.mp4`. Review it before replacing the current copy in `docs/media/`. Fresh recording calls live APIs and can incur provider usage. Recorders never submit reports or initiate emergency calls; temporary sharing sessions must be revoked.

The legacy silent demo, its recorder and discarded takes have been removed. Current scripts and editable source captures are retained; previous tracked versions remain available in Git history.
