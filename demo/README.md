# SafeHer demo

- [Watch the current 2:54 demo](../docs/media/safeher-final-demo.mp4)
- [English captions](../docs/media/safeher-final-demo.srt)
- [Full narration](NARRATION.md)
- [Production details and limitations](FINAL_DEMO.md)
- [Source storyline](../docs/DEMO_STORYLINE.md)

The latest cut follows Meera’s walk home from Kuvempu Road metro. It uses complete-sentence female Indian-English narration at normal speed, verified UI stills and editorial evidence panels. Sharing is simulated GPS and status-only; Maps grounding is a labeled transcript of the captured response.

## Current production files

- `final_story.json`: current narration and 174-second scene timings.
- `build_demo.py`: current composition and publication pipeline.
- `capture_demo.py`: live deployed-app recorder.
- `final_edit.py`: shared voice, caption, overlay and encoding utilities.
- `final_support_capture.py`: sharing capture helpers used by the recorder.

```bash
uv run --no-sync python demo/build_demo.py
```

Rebuilding needs the retained ignored capture under `demo/out/updated/`, Chrome/Playwright for fresh recording, ffmpeg, Pillow and edge-tts. No new live API calls are needed to re-edit the existing capture. Fresh captures require provider access and may incur costs.

Only the latest video, captions, poster and storyline are retained. Prior tracked versions remain available in Git history.
