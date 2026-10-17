# Demo storyline: "Reached?" (about 3 minutes)

Every woman in India knows the message: **"Reached?"** Every night, someone who loves her waits for one word back. SafeHer is about the 12 minutes between the station and that reply.

**Meera, 24**, moved to Bengaluru from Mysuru for her first job. Her shift ends late. At **10:00 PM** she steps out of **Mahakavi Kuvempu Road metro** (Rajajinagar). Her PG is behind **Sri Vani Girls High School**, about 1 km away. A map app says 12 minutes. It doesn't say what those 12 minutes look like.

All numbers below are real SafeHer output for that walk (17 Oct 2026 data). Nothing is staged except the simulated GPS in the live-sharing scene, which is labelled.

| # | Time | Scene | What the viewer sees | Caption / voice-over |
|---|---|---|---|---|
| 1 | 0:00–0:12 | **Cold open** | Black screen. A phone notification slides in: *Amma: Reached?* Then one line of data. | "Bengaluru police recorded **1,135 molestation cases in 2023, nearly double 2021's 573**." *(Bengaluru City Police, Crime in Bengaluru 2023)* |
| 2 | 0:12–0:25 | **The walk home** | Map of Rajajinagar at night. Pin at the metro exit, pin at her PG. Search typed: *Mahakavi Kuvempu Road* → *Sri Vani Girls High School*. | "10 PM. Meera's PG is 1 km from the metro. Every map can tell her how fast. None can tell her what's on the way." |
| 3 | 0:25–0:50 | **Two minutes, a different walk** | Three routes appear. **Shortest: 12 min, 66.** **Most support: 14 min, 89.** The shortest line cuts through side lanes. | "Two extra minutes. Nearly five times as many streets mapped as lit (13% → 63%). Three times as many places open on the way (10 → 33)." |
| 4 | 0:50–1:15 | **See why** | Night-light glow, lit streets and lamps switch on. *How is this calculated?*: the formula with its real numbers, where each signal's data came from. | "NASA night-light satellites, OpenStreetMap lamps, live opening hours and transit. Every point of the score traces back to data you can open." |
| 5 | 1:15–1:30 | **If something feels wrong** | Plan B on each route. | "On the shortest route, the nearest place likely to be open can be **5.4 minutes** away. On Most support, never more than **2.1**, with Subramanyanagar Police Station on the way." |
| 6 | 1:30–1:55 | **11:30 PM** | Drag the time to 11:30 PM. Scores drop. A **653 m pink stretch** lights up on the shortest route, two-thirds of the walk. | "Same street. Same 12 minutes. But at 11:30 the shops are shut. **The street didn't change. The city around it did.** Shortest: 46. Most support: 59." |
| 7 | 1:55–2:25 | **Ask HerSide** | Right panel, *Gemini 2.5 Flash · Vertex AI*. (a) "Is the longer route worth 2 minutes?" → a comparison from SafeHer's own numbers. (b) "Is there a pharmacy open near Sri Vani school now?" → **grounded in Google Maps**, sources listed. (c) "Recommend a movie for tonight?" → politely declines: walking safety only. | "HerSide answers from SafeHer's calculations and Google Maps, and stays on her walk home." |
| 8 | 2:25–2:45 | **Someone walking with her** | *Share live location* → Amma's viewer link (simulated GPS, labelled). Then *I don't feel safe* → **112** in one tap, no AI in between. | "Amma watches the dot move. Sharing ends when Meera says so. SOS never waits for a model." |
| 9 | 2:45–3:00 | **Reached** | Phone: *Meera: Reached ❤️*. Title card. | "SafeHer. Not the fastest way home: the one with more light, more people and more help along it. All of Delhi and Bengaluru, 1.6 million street segments, open data and Gemini on Vertex AI. Better-informed walks, never a promise of safety." |

## Why this walk
- **Real and ordinary:** a metro station to a PG, 1 km, 12–14 minutes. It's the walk thousands of women make every night, not a 37-minute trek nobody takes.
- **The contrast is in the data, not the script:** found by scoring 35 metro → PG/hostel walks across Bengaluru at 10 PM and ranking by the gap between the shortest route and the best one. This walk had the largest gap for the smallest detour.
- **Time matters:** the jump from 10 PM to 11:30 PM shows what no static map shows: the same street loses its open shops, and with them its support.

### Numbers used (planner output, departure 17 Oct 2026)
| | 10:00 PM Shortest | 10:00 PM Most support | 11:30 PM Shortest | 11:30 PM Most support |
|---|---|---|---|---|
| Time / distance | 12 min / 999 m | 14 min / 1,142 m | 12 min / 999 m | 13 min / 1,016 m |
| Score | 66 | 89 | 46 | 59 |
| Mapped as lit | 13% | 63% | 13% | 46% |
| Places likely open on the way | 10 | 33 | 4 | 6 |
| Weak stretches | none | none | 653 m | 133 m |
| Longest walk to likely-open help (Plan B) | 5.4 min | 2.1 min | 5.4 min | 3.5 min |

Backups with similar contrast: Cubbon Park metro → Locul Hostel Central (11 vs 12 min, 71 vs 81 at 10:30 PM); Krishna Rajendra Market metro → a PG on the market side (11 vs 13 min, 66 vs 76).

## Words to keep, words to avoid
- Say "more support", "better lit", "more places open", "weak stretch". Never call a street or area "safe", "unsafe" or "dangerous". The app enforces this for Gemini; the pitch follows the same rule.
- The cold-open statistic is city-wide context. SafeHer doesn't use it to score streets, because no district split is published.

## Before recording
- Check that Gemini 2.5 explanations pass the fact checks on this walk (the signal-difference fix of 17 Oct should cover "open places score 27 vs 88").
- **Cost:** one run is about 15–20 Gemini calls (explanations, three HerSide questions and their scope checks) plus a few Maps grounding queries. Allow 2–3 runs. Ask before running.
