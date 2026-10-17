"""Record the "Reached?" demo (docs/DEMO_STORYLINE.md): Meera's real 1 km walk home from
Mahakavi Kuvempu Road metro to Sri Vani Girls High School, Rajajinagar, at 10 PM and 11:30 PM.

From the repository root, with SafeHer running on Vertex AI and the Google Maps key (real map):
    backend/.venv/bin/python demo/record_story.py --base http://localhost:8765

Everything on screen is live: routing, scores, Gemini 2.5 explanations, HerSide and Maps grounding.
Only the live-sharing GPS is simulated, and labelled. Captions read their numbers from the app,
so they always match what is shown. One run makes roughly 15-20 Gemini calls.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

import record_demo as rd
from record_demo import Demo

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
NAME = "safeher-demo-reached"
SIZE = {"width": 1440, "height": 900}
ORIGIN = (12.99847, 77.55687, "Mahakavi Kuvempu Road Metro")
DEST = (13.00479, 77.55332, "Sri Vani Girls High School")
TEN_PM, HALF_ELEVEN = 22 * 60, 23 * 60 + 30
GPS = {"latitude": ORIGIN[0], "longitude": ORIGIN[1], "accuracy": 6}
BASE = "http://localhost:8765"


def trip_url(minutes: int) -> str:
    enc = lambda p: f"{p[0]:.5f},{p[1]:.5f},{p[2].replace(' ', '%2520')}"
    return f"{BASE}/#city=bengaluru&o={enc(ORIGIN)}&d={enc(DEST)}&t={minutes}"


CARD_JS = """
(html) => {
  let card = document.querySelector('#story-card');
  if (!html) { if (card) { card.style.opacity = 0; setTimeout(() => card.remove(), 700); } return; }
  if (!card) {
    card = document.createElement('div'); card.id = 'story-card';
    card.style.cssText = 'position:fixed;inset:0;z-index:100000;display:flex;flex-direction:column;'
      + 'align-items:center;justify-content:center;gap:28px;padding:80px;text-align:center;'
      + 'background:radial-gradient(circle at 75% 20%,#3A3152 0%,#121A2B 60%);color:#F6F1EA;'
      + 'font:500 30px/1.45 "DM Sans",system-ui,sans-serif;opacity:0;transition:opacity .7s ease';
    document.body.append(card);
    requestAnimationFrame(() => card.style.opacity = 1);
  }
  card.innerHTML = html;
}
"""
BUBBLE = ('<div style="background:#2A3554;border-radius:22px;padding:18px 26px;text-align:left;min-width:300px;'
          'box-shadow:0 10px 40px rgba(0,0,0,.4)"><div style="color:#F2A541;font-size:18px;font-weight:700">{who}</div>'
          '<div style="font-size:34px;font-weight:700">{text}</div></div>')
FINE = '<div style="font-size:18px;color:#8E95AB;max-width:900px">{}</div>'


class Story(Demo):
    def overlay(self) -> None:
        self.page.evaluate(rd.OVERLAY_JS)
        self.page.evaluate("([x, y]) => __demo.move(x, y)", list(self.pos))

    def card(self, html: str | None, hold: float = 0.0) -> None:
        self.page.evaluate(CARD_JS, html)
        if hold:
            self.wait(hold)

    def caption_in(self, dialog: str) -> None:
        """Captions must live inside an open <dialog> (top layer) to show over its backdrop."""
        self.page.evaluate("(d) => { const c = document.querySelector('#demo-caption');"
                           " document.querySelector(d).append(c); c.style.marginLeft = '0'; }", dialog)

    def caption_out(self) -> None:
        self.page.evaluate("() => { const c = document.querySelector('#demo-caption');"
                           " document.body.append(c); c.style.marginLeft = '204px'; }")


def routes(page) -> dict:
    return page.evaluate("""() => Object.fromEntries(state.plan.routes.map((r, i) => [r.label, {
        i, min: r.duration_min, score: r.score, lit: Math.round(r.highlights.lit_confirmed_share * 100),
        open: Object.values(r.highlights.open_places_passed).reduce((a, b) => a + b, 0),
        weak: r.weak_stretches.reduce((a, w) => a + w.length_m, 0), help: r.plan_b && r.plan_b.max_walk_min,
        dist: r.distance_m }]))""")


def wait_explanation(demo: Story) -> str:
    page = demo.page
    page.wait_for_function("() => document.querySelector('#aiCard .ai-text')", timeout=90000)
    text = page.locator("#aiCard .ai-text").inner_text()
    if "Couldn’t load the explanation" in text:
        raise RuntimeError(f"Explanation failed: {text}")
    return text


def ask(demo: Story, question: str | None, timeout: int = 90000) -> dict:
    page = demo.page
    if question:
        demo.click("#chatbotInput", pause=.2)
        page.keyboard.type(question, delay=35)
    with page.expect_response(lambda r: r.url.endswith("/api/chat") and r.request.method == "POST",
                              timeout=timeout) as pending:
        demo.click("#chatbotSend", pause=.2)
    body = pending.value.json()
    demo.wait(1.5)
    page.locator("#chatbotMessages").evaluate("el => el.scrollTop = el.scrollHeight")
    return body


def opening(demo: Story) -> None:
    page = demo.page
    page.goto("about:blank")
    page.set_content('<html><body style="margin:0;background:#121A2B"></body></html>')
    demo.overlay()
    demo.card(BUBBLE.format(who="Amma · 10:01 PM", text="Reached?"), 3)
    demo.card(BUBBLE.format(who="Amma · 10:01 PM", text="Reached?")
              + '<div style="font-size:30px;max-width:980px;margin-top:20px">Bengaluru police recorded <b style="color:#F2A541">'
                '1,135 molestation cases in 2023</b>, nearly double the 573 in 2021.</div>'
              + FINE.format("Bengaluru City Police, Crime in Bengaluru 2023 (via OpenCity). City-wide context."), 5)
    demo.card('<div style="font:600 56px/1.15 Georgia,serif;max-width:1100px">10 PM. Meera steps out of '
              'Mahakavi Kuvempu Road metro.<br>Her PG is 1 km away.</div>'
              '<div style="color:#C9CCD6;max-width:900px">Every map can tell her how fast. None can tell her what’s on the way.</div>', 5)


def walk_at_ten(demo: Story) -> dict:
    page = demo.page
    page.goto(trip_url(TEN_PM), wait_until="domcontentloaded")
    demo.overlay()
    demo.card('<div style="font:600 56px/1.15 Georgia,serif">SafeHer</div>'
              '<div style="color:#C9CCD6">Walking routes, scored for what’s along the way</div>')
    demo.wait_for_plan(extra=1.5)
    demo.card(None, 1)
    r = routes(page)
    best, short = r["Most support"], r["Shortest"]
    assert page.evaluate("state.plan.routes[0].label") == "Most support", "Expected Most support first"
    demo.caption("Mahakavi Kuvempu Road metro → Sri Vani Girls High School, Rajajinagar. <b>10:00 PM</b>.", 4)
    google = " (one is Google’s own, scored by the same rules)" if "Google route" in r else ""
    demo.caption(f"{len(r)} routes{google}, every 50 m checked. <b>Shortest: {short['min']} min, score {short['score']}</b>.", 5)
    demo.caption(f"<b>Most support: {best['min']} min, score {best['score']}</b>. "
                 f"{best['min'] - short['min']} extra minutes.", 4)
    demo.caption(f"Streets mapped as lit: <b>{short['lit']}% → {best['lit']}%</b>. "
                 f"Places likely open on the way: <b>{short['open']} → {best['open']}</b>.", 5)
    text = wait_explanation(demo)
    page.locator("#aiCard").scroll_into_view_if_needed()
    demo.caption("Gemini 2.5 on Vertex AI explains the choice, <b>checked against this route’s data</b>: "
                 "no invented numbers or places.", 6)
    print("Explanation (10 PM):", text, flush=True)
    return r


def why(demo: Story, r: dict) -> None:
    page = demo.page
    page.evaluate("map.easeTo({ zoom: 16, duration: 2500 })")
    demo.wait(3)
    page.wait_for_function("map.getSource('overlays') && map.querySourceFeatures('overlays')"
                           ".some(f => f.properties.kind === 'night_light')", timeout=30000)
    demo.caption("At night the map shows <b>NASA satellite night lights</b>, streets mapped as lit, and street lamps.", 5)
    page.evaluate("map.easeTo({ zoom: 15.2, duration: 1500 })")
    demo.wait(1.5)
    page.locator(".backup").first.scroll_into_view_if_needed()
    best, short = r["Most support"], r["Shortest"]
    demo.caption(f"Plan B: on the shortest route the nearest likely-open place can be <b>{short['help']} min</b> away. "
                 f"On Most support, never more than <b>{best['help']} min</b>.", 6)
    demo.click("#explainBtn", pause=1)
    demo.caption_in("#explainDialog")
    demo.caption("<b>How is this calculated?</b> The formula with this route’s own numbers.", 4)
    for _ in range(3):
        page.evaluate("() => document.querySelector('#explainBody').scrollBy({ top: 260, behavior: 'smooth' })")
        demo.wait(1.6)
    demo.caption("Where each signal came from, and the lamps, stops and open places counted. <b>No black box.</b>", 5)
    page.keyboard.press("Escape")
    demo.caption_out()
    demo.wait(.5)


def half_eleven(demo: Story) -> None:
    page = demo.page
    page.locator("#view").evaluate("el => el.scrollTop = 0")
    demo.caption("Now the same walk at <b>11:30 PM</b>.", 2)
    demo.slide_time(HALF_ELEVEN, step=30, delay=.3)
    r = routes(page)
    best, short = r["Most support"], r["Shortest"]
    demo.click(f".route-card[data-i='{short['i']}']", pause=1.2)
    demo.wait_for_plan(extra=.5)
    demo.caption(f"Shortest: <b>{short['score']}</b>. Most support: <b>{best['score']}</b>. "
                 f"Places likely open on the shortest route: <b>{short['open']}</b>.", 5)
    if page.locator("[data-weak='0']").count():
        demo.click("[data-weak='0']", pause=1.5)
        demo.caption(f"<b>{short['weak']} m</b> of the {short['dist']} m walk is now a weak stretch: "
                     "few open places, no transit nearby.", 5)
    demo.caption("<b>The street didn’t change. The city around it did.</b>", 4)
    demo.click(f".route-card[data-i='{best['i']}']", pause=1.5)
    wait_explanation(demo)


def herside(demo: Story) -> None:
    page = demo.page
    demo.click("#chatbotBtn", pause=1)
    demo.caption_in("#chatbotDialog")
    model = page.locator("#chatbotModel").inner_text()
    demo.caption(f"<b>HerSide</b>, her companion: {model}.", 3)
    demo.click("#chatbotTrip", pause=1)
    demo.caption("One tap attaches tonight’s walk. <b>Is the longer route worth it?</b>", 3)
    body = ask(demo, None)
    assert body.get("intent") == "pre_trip", f"Expected a route comparison, got {body.get('intent')}"
    demo.caption("Answered from SafeHer’s own calculations: time, scores, lighting and weak stretches.", 7)
    body = ask(demo, "Is there a pharmacy open near Sri Vani Girls High School, Rajajinagar right now?")
    sources = len(body.get("sources") or [])
    print("Pharmacy answer sources:", sources, flush=True)
    demo.caption("Places are <b>grounded with Google Maps</b>, with sources listed." if sources
                 else "Nearby places come from live map data.", 7)
    body = ask(demo, "Can you recommend a good movie to watch tonight?")
    demo.caption("Off-topic? HerSide politely declines. <b>It only talks about her walk.</b>", 5)
    page.keyboard.press("Escape")
    demo.caption_out()


def sharing(demo: Story, browser, sessions: list) -> None:
    page = demo.page
    route = page.evaluate("state.plan.routes[state.selected].geometry")  # [[lat, lon], ...]
    points = [route[int(len(route) * f)] for f in (0.15, 0.35, 0.55)]
    demo.click("#sharingBtn", pause=.8)
    demo.caption_in("#sharingDialog")
    demo.caption("Meera shares her live location with Amma. <b>GPS is simulated in this demo.</b>", 4)
    demo.click("#startSharing", pause=1)
    page.wait_for_function("document.querySelector('#sharingLink').value.includes('/live#')")
    session = page.evaluate("JSON.parse(sessionStorage.getItem('safeher-sharing'))")
    sessions.append(session)
    viewer_url = page.locator("#sharingLink").input_value()
    demo.caption("A private link. No app to install. <b>No location history</b>: only the latest point.", 4)
    sender_ctx = browser.new_context(viewport=SIZE, geolocation=GPS, permissions=["geolocation"], locale="en-IN")
    try:
        sender = sender_ctx.new_page()
        sender.goto(BASE, wait_until="domcontentloaded")
        sender.evaluate("s => sessionStorage.setItem('safeher-sharing', JSON.stringify(s))", session)
        sender.reload(wait_until="domcontentloaded")
        sender.locator("#sharingBtn").click()
        sender.get_by_role("button", name="Resume location updates").click()
        page.goto(viewer_url, wait_until="domcontentloaded")
        demo.overlay()
        rd.label_simulation(page)
        page.evaluate("() => { for (const b of document.querySelectorAll('div')) if (b.textContent.startsWith('DEMO · SIMULATED GPS')) b.textContent = 'DEMO · SIMULATED GPS'; }")
        page.wait_for_function("document.querySelector('#liveStatus').textContent.includes('GPS accuracy')")
        demo.caption("Amma’s screen: the route, Meera’s latest position and how fresh it is.", 4)
        demo.click("#followLocation")
        for lat, lon in points:
            sender_ctx.set_geolocation({"latitude": lat, "longitude": lon, "accuracy": 7})
            demo.wait(6)
            demo.click("#followLocation", pause=.5)
    finally:
        sender_ctx.close()
    page.goto(trip_url(TEN_PM), wait_until="domcontentloaded")
    demo.overlay()
    demo.wait_for_plan()
    demo.click("#sharingBtn", pause=.6)
    demo.caption_in("#sharingDialog")
    demo.click("#stopSharing", pause=1)
    page.wait_for_function("document.querySelector('#sharingStatus').textContent.includes('Sharing stopped')")
    demo.caption("Home. <b>Stop sharing</b> ends the link and deletes the stored position.", 4)
    page.keyboard.press("Escape")
    demo.caption_out()
    demo.click("#sosBtn", pause=1)
    demo.caption_in("#sosDialog")
    demo.caption("And if she ever needs it: <b>112 in one tap</b>. No AI in the way.", 4)
    page.keyboard.press("Escape")
    demo.caption_out()
    demo.caption(None)


def closing(demo: Story) -> None:
    demo.card(BUBBLE.format(who="Amma · 10:01 PM", text="Reached?")
              + '<div style="background:#F2A541;color:#121A2B;border-radius:22px;padding:18px 26px;text-align:left;'
                'min-width:300px;align-self:center"><div style="font-size:18px;font-weight:700">Meera · 10:15 PM</div>'
                '<div style="font-size:34px;font-weight:700">Reached ❤</div></div>', 4)
    demo.card('<div style="font:600 64px/1.15 Georgia,serif;max-width:1150px">Not the fastest way home.<br>'
              'The one with more light, more people and more help along it.</div>'
              '<div style="color:#F2A541;font-weight:700;font-size:34px">SafeHer</div>'
              '<div style="color:#C9CCD6">All of Delhi and Bengaluru · 1.6 million street segments · '
              'open data + Gemini on Vertex AI</div>'
              + FINE.format("Better-informed walks, never a promise of safety. In an emergency in India, call 112."), 7)


def main() -> None:
    global BASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    BASE = rd.BASE = args.base.rstrip("/")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        import imageio_ffmpeg  # dev dependency; ships its own ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    MEDIA.mkdir(exist_ok=True)
    sessions: list = []
    with tempfile.TemporaryDirectory(prefix="safeher-story-") as temp, sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed,
                                    args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        context = browser.new_context(viewport=SIZE, device_scale_factor=1, record_video_dir=temp,
                                      record_video_size=SIZE, timezone_id="Asia/Kolkata", locale="en-IN",
                                      geolocation=GPS, permissions=["geolocation", "clipboard-write"])
        page = context.new_page()
        page.set_default_timeout(60000)
        errors: list = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        video = page.video
        poster = Path(temp) / "poster.jpg"
        try:
            demo = Story(page)
            opening(demo)
            r = walk_at_ten(demo)
            page.screenshot(path=str(poster))
            why(demo, r)
            half_eleven(demo)
            herside(demo)
            sharing(demo, browser, sessions)
            closing(demo)
            if errors:
                raise RuntimeError(f"Browser JavaScript errors during recording: {errors[:3]}")
        finally:
            for s in sessions:
                context.request.delete(f"{BASE}/api/trips/{s['viewer_token']}",
                                       headers={"Authorization": f"Bearer {s['owner_token']}"})
            context.close()
            browser.close()
        staged = Path(temp) / "story.mp4"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", video.path(), "-an", "-vf", "fps=25",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
                        str(staged)], check=True)
        shutil.copyfile(staged, MEDIA / f"{NAME}.mp4")
        shutil.copyfile(poster, MEDIA / f"{NAME}-poster.jpg")
    print(f"Recorded: {MEDIA / f'{NAME}.mp4'}", flush=True)


if __name__ == "__main__":
    main()
