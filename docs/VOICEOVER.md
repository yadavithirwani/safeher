# Voice-over for the "Reached?" demo (ElevenLabs)

Timed to `docs/media/safeher-demo-reached.mp4` (4:02). Each row is **one ElevenLabs clip**: generate it, then drop it on the timeline at its start time. Every line fits its window at a calm pace (about 2.3 words a second), so there is breathing room; silence in the gaps is intentional.

**Suggested settings:** Eleven Multilingual v2, a warm, unhurried Indian-English female voice (search the Voice Library for "Indian English narrator"). Stability 50, Similarity 75, Style 25, Speaker boost on. Numbers are written as words so they're read naturally.

| # | Start | Ends by | On screen | Voice-over (paste into ElevenLabs) |
|---|---|---|---|---|
| 1 | 0:00 | 0:03 | "Amma: Reached?" | Every night, someone waits for one word. |
| 2 | 0:03 | 0:08 | Police statistic | In Bengaluru, reported molestation cases nearly doubled in just two years. |
| 3 | 0:08 | 0:13 | "10 PM. Meera steps out…" | Ten PM. Meera steps off the metro. Her PG is one kilometre away. |
| 4 | 0:14 | 0:20 | SafeHer title, map loads | Every map can tell her how fast. SafeHer shows her what's on the way. |
| 5 | 0:20 | 0:29 | Route, then "4 routes…" | SafeHer checks every fifty metres of every route: lighting, open places, transit and past incidents. |
| 6 | 0:29 | 0:38 | "Most support: 14 min, score 89" | For just two more minutes, this route scores eighty-nine, not sixty-six. Nearly five times the lit streets. Three times the open places. |
| 7 | 0:38 | 0:47 | Gemini explanation | Gemini on Vertex AI explains the choice in plain words, and every number it uses is checked against the route's own data. |
| 8 | 0:47 | 0:54 | Map zooms: night lights | NASA satellite night lights, street lamps and lit streets, all on one map. |
| 9 | 0:54 | 1:01 | Plan B | If something feels wrong, help is never more than two minutes away on this route. |
| 10 | 1:01 | 1:16 | "How is this calculated?" | And nothing is a black box. See the formula, the data behind every signal, and every lamp, stop and open place that was counted. |
| 11 | 1:16 | 1:23 | Time slider moves | Now, the same walk at eleven thirty. |
| 12 | 1:23 | 1:37 | Scores drop, pink stretch | The shops have closed. The shortest route drops to forty-six, and two-thirds of it becomes a weak stretch: few open places, no transit nearby. |
| 13 | 1:37 | 1:42 | "The street didn't change…" | The street didn't change. The city around it did. |
| 14 | 1:42 | 1:52 | HerSide opens | Meet HerSide, Meera's companion, powered by Gemini two point five on Vertex AI. |
| 15 | 1:52 | 2:00 | "One tap attaches tonight's walk" | One tap attaches tonight's walk. Is the longer route worth it? |
| 16 | 2:03 | 2:14 | Answer appears | HerSide answers from SafeHer's own calculations: the time, the scores, the lighting and the weak stretches. |
| 17 | 2:26 | 2:41 | Cafés question, Google Maps sources | Need somewhere open and busy late? HerSide asks Google Maps, and shows every source it used. |
| 18 | 2:41 | 2:46 | Movie question declined | Ask it about a movie, and it politely declines. It only talks about her walk. |
| 19 | 2:46 | 2:58 | Share live location | Meera shares her live location with Amma. A private link, no app to install, and no location history. |
| 20 | 3:02 | 3:14 | Amma's screen | On Amma's screen: the route, Meera's latest position, and how fresh it is. |
| 21 | 3:18 | 3:28 | Dot moves along the route | Step by step, all the way home. |
| 22 | 3:34 | 3:42 | "Home. Stop sharing…" | Home. Stopping sharing ends the link and deletes her location. |
| 23 | 3:42 | 3:47 | SOS: 112 | And if she ever needs it, one one two is a single tap away. No AI in the way. |
| 24 | 3:47 | 3:51 | "Meera: Reached ❤" | *(beat)* Reached. |
| 25 | 3:51 | 4:01 | Closing card | SafeHer. Not the fastest way home. The one with more light, more people, and more help along it. |

## Notes
- **Clip 24** is the emotional payoff: generate it on its own with Stability around 35 so it sounds soft, or record it yourselves.
- **Clips 16–17:** the gap between 2:14 and 2:26 is HerSide's answer on screen; leave it silent so judges read it.
- **One file instead of clips:** paste [voiceover-elevenlabs.txt](voiceover-elevenlabs.txt) (the same lines with `<break>` tags timed to the video) into ElevenLabs with Multilingual v2. If a long run of breaks causes glitches, split the text at those runs and generate it in parts.
- Keep music under the voice at about −20 dB; drop it out completely for clip 24.
- Every figure in the script is on screen in the video or from the cited police source. If the demo is re-recorded, check the timings: captions start at the times above to within a second.
