# Final demo production brief

**Target: 2:50 (170 seconds), strictly below 3:00.**

## Story

A night out should end with memories, not a mental checklist of dark streets and places to ask for help. Priya leaves a friend's birthday at Toit, Indiranagar, Bengaluru at 9:30 PM and wants to walk to her PG near Bob's Bar in Doopanahalli. Each feature answers a question she has along that journey.

The app's location search uses Bob's Bar as a nearby landmark, not Priya's actual home. Priya is a fictional demo persona; sharing uses visibly labeled simulated browser GPS.

## Edit decision list

| Time | Scene | Proof on screen |
|---|---|---|
| 0:00–0:15 | Motivation | Opening title and the shortest-route question |
| 0:15–0:35 | Priya's trip | Bengaluru, Toit → Bob's Bar, 21:30; real route alternatives |
| 0:35–0:50 | Time and lighting | Departure change; time-aware route evidence |
| 0:50–1:10 | Transparent scores + Gemini | Formula, source evidence, checked Gemini explanation |
| 1:10–1:23 | Plan B | Reachable help coverage and disclosed limitations |
| 1:23–1:52 | HerSide | Selected-trip attachment; genuine computed route comparison |
| 1:52–2:07 | Maps grounding | Practical venue-context answer with returned sources |
| 2:07–2:26 | Private live sharing | Consent, labeled simulated GPS, contact status, stop sharing |
| 2:26–2:35 | Urgent support | 112 information, without starting a call |
| 2:35–2:50 | Close | Delhi / Bengaluru coverage and honest promise |

## HerSide question

> For this walk, is the extra walking time worth it for better lighting and fewer weak stretches?

Use **Compare my selected trip** so the origin, destination and departure are attached as structured context. Record a real response. Don't script numeric claims before the actual plan is known.

Optional second question:

> At Toit after 10pm, what safety support is available from staff, and is the venue open then?

Include this only if the real response has attributable Maps sources. If not, revise the narration and scene budget; never show a generic answer while claiming grounding.

## Audio and pacing

- Female Indian-English neural narration: `en-IN-NeerjaNeural`.
- Script source: `demo/final_story.json`.
- Synthesized scene files: `demo/out/final/audio/`.
- Avoid speeding narration above 1.18×. Rewrite long sentences instead.
- Compress typing/loading/repeated movement; leave key evidence and HerSide results readable.
- No music necessary: clear voice, real UI, and restrained chapter headings.
- Scene-timed SRT and a selectable MP4 subtitle track are generated; timing is approximate, not word-level alignment.

## Production tools

- `demo/final_capture.py`: real deployed-app browser capture, scene clips, manifest, session cleanup.
- `demo/final_edit.py --audio-only`: synthesize narration and inspect timing.
- `demo/final_edit.py`: assemble captures, chapter headings, narration and subtitles; verify runtime and decode output.
- Generated files remain under ignored `demo/out/final/` until reviewed.
- The legacy demo has been removed; the current review cut is retained in `docs/media/`.

## Current review cut

- Current copy: `docs/media/safeher-final-demo.mp4`; editable output: `demo/out/final/safeher-final-demo.mp4`; measured runtime **170.033 seconds (2:50)**.
- 1920×1080 H.264 video, female neural AAC narration, selectable English subtitle track and separate SRT.
- Actual deployed Gemini explanation, pre-trip tool response and Maps-grounded response captured successfully.
- Sharing validated with real location PUT/GET and post-revocation 404; browser GPS is simulated and visibly labeled.
- **Recording-network limitation:** CARTO DNS fails, so the contact viewer is shown as a status-only crop with an explicit basemap-unavailable disclosure. No rendered contact map is claimed.
- The SOS dialog and enlarged viewer status use freeze frames extracted from the successful live capture for readability. These are real UI states, not constructed answers.
- Not every button is demonstrated: report submission is deliberately avoided; nearby assistance appears through Plan B and HerSide. The original storyline's extra refusal question is omitted to preserve the time budget.
- This is a review cut until the owner approves the network-limited viewer presentation. Previous demo versions and the legacy recorder have been removed.

## Release checks

- [ ] Runtime measured by ffprobe is strictly below 180 seconds.
- [ ] Full video decodes without errors; audio exists and is intelligible.
- [ ] Inspect every scene for legible evidence, correct crop and transition timing.
- [ ] HerSide is a real pre-trip response, not unavailable/fallback text.
- [ ] Gemini explanation is genuinely Gemini-backed and checked.
- [ ] Grounding claim matches visible citations.
- [ ] Location simulation is visibly labeled on sender and viewer.
- [ ] Viewer credentials are not exposed; all temporary sessions are revoked.
- [ ] No reports submitted and no phone calls initiated.
- [ ] No claims of guaranteed safety, background tracking or emergency dispatch.

This brief adapts [the original storyline](../docs/DEMO_STORYLINE.md); it does not replace it.
