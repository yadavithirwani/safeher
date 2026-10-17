# Demo storyline (draft, about 3½ minutes)

One person, one evening, one decision: **Priya leaves a friend's birthday at Toit, Indiranagar, at 9:30 PM and wants to walk to her PG near Bob's Bar.** Every feature appears because she needs it at that moment, not as a tour of buttons. The current recording (`demo/record_demo.py`) walks feature by feature; this script reorders and trims it around that story.

| # | Time | Scene | What the viewer sees | Line on screen |
|---|---|---|---|---|
| 1 | 0:00–0:15 | **The question maps don't ask** | Bengaluru, 9:30 PM, dark map. Toit → Bob's Bar typed into search (neighbourhoods shown under each result). | "Maps answer *how fast*. At 9:30 PM, Priya also wants to know *what's along the way*." |
| 2 | 0:15–0:45 | **Three routes, scored** | Shortest, Most support and Alternative appear (plus Google's own route, scored by the same rules). Score badges, weak stretches in pink. | "Each route is checked every 50 m for lighting, transit, open places, past incidents and community reports." |
| 3 | 0:45–1:05 | **Why the night looks different** | Zoom in: NASA night-light glow, streets mapped as lit, street lamps. Slide the time from 6 PM to 11:45 PM; scores move. | "Satellite night lights, mapped lamps and lit streets. At 11:45 PM fewer places are open, and the scores change." |
| 4 | 1:05–1:40 | **Nothing is a black box** | Open *How is this calculated?* on Most support: the formula with its numbers (average of 145 stretches × 0.8 + weakest 10% × 0.2 = 97), lighting from 49% NASA / 40% lit tags / 11% lamps (83 lamps), the stops and open places used. Then the Gemini explanation with "Checked against this route's data". | "Every point of the score traces back to data you can see. Gemini only puts it into words, and it is fact-checked." |
| 5 | 1:40–2:00 | **If she needs to stop** | Tap the weak stretch; Plan B: nearest likely-open help within 3 minutes; police and pharmacy along the route. | "Plan B: how far to a place that's likely open, all along the walk." |
| 6 | 2:00–2:50 | **Ask HerSide** | Right-side panel, *Gemini 2.5 Flash · Vertex AI*. (a) "Is the longer route worth it?" → measured comparison (2 extra minutes, 88 vs 76). (b) "Which cafés near here stay open after 10 pm?" → answer **grounded with Google Maps**, sources listed. (c) "Is the beer good at Toit?" → polite refusal: safety only. | "HerSide answers from SafeHer's calculations and Google Maps, and only about safety." |
| 7 | 2:50–3:20 | **Walking, with someone watching out** | Share live location → sister's viewer link (simulated GPS, labelled), STALE marker, stop sharing. Tap *I don't feel safe* → 112 immediately, no AI in the way. | "Opt-in sharing that ends when she says so. SOS never waits for a model." |
| 8 | 3:20–3:35 | **Close** | Delhi switch for one beat (whole city), then the title card. | "All of Delhi and Bengaluru, 1.6 million street segments, open data plus Gemini on Vertex AI. Better-informed walks; never a promise of safety." |

## What changes from the current recording
- **One story:** Bengaluru with a person and a purpose, instead of jumping between Delhi and Bengaluru.
- **Leads with the new strengths:** whole-city search, night lights, the score evidence, HerSide on Vertex with Maps grounding and its guardrail refusal.
- **Shorter:** about 3½ minutes, down from 6:20. The Plan B and live-sharing chapters are cut to their key moments.
- **Explanations and HerSide run live on Gemini 2.5**, no longer the template fallback, now that Vertex works.

## Before recording
- Bob's Bar has several branches; the script picks the one in Doopanahalli (Indiranagar), about 1.3 km from Toit.
- Check that Gemini 2.5 Flash explanations pass the fact checks reliably. One of 2 failed in a quick check on 17 Oct (a number not in the data); fix the prompt first if that repeats.
- **Cost:** one recording run is roughly 15–20 Gemini calls (explanations, HerSide's three questions and their scope checks) plus a few Google Maps grounding queries. Allow 2–3 runs.
