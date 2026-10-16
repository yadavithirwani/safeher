"""Record a captioned walkthrough of the SafeHer demo with Playwright.

Usage (server must be running; --base or SAFEHER_URL, default http://localhost:8765):
    backend/.venv/bin/python demo/record_demo.py            # → demo/out/safeher-demo.mp4
    backend/.venv/bin/python demo/record_demo.py --headed   # watch it while it records

Captions and a visible cursor are injected into the page, because Playwright's video doesn't
show the real mouse. The script never submits a report, so it leaves no data behind.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from urllib.parse import quote, urlencode

import httpx
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

DEFAULT_BASE = os.environ.get("SAFEHER_URL", "http://localhost:8765")
OUT_DIR = Path(__file__).resolve().parent / "out"
SIZE = {"width": 1440, "height": 900}

OVERLAY_JS = """
(() => {
  const css = document.createElement('style');
  css.textContent = `
    #demo-cursor { position: fixed; z-index: 99999; width: 22px; height: 22px; margin: -11px 0 0 -11px;
      border-radius: 50%; background: rgba(255,255,255,.9); border: 3px solid #5B2A86; pointer-events: none;
      box-shadow: 0 2px 8px rgba(0,0,0,.35); transition: transform .12s ease; left: -50px; top: -50px; }
    #demo-cursor.down { transform: scale(.7); background: #F2A541; }
    #demo-caption { position: fixed; z-index: 99998; left: 50%; bottom: 36px; transform: translateX(-50%);
      max-width: 860px; padding: 14px 22px; border-radius: 14px; background: rgba(27,27,31,.88); color: #fff;
      font: 500 20px/1.4 Roboto, system-ui, sans-serif; text-align: center; pointer-events: none;
      box-shadow: 0 6px 24px rgba(0,0,0,.35); opacity: 0; transition: opacity .35s ease; margin-left: 204px; }
    #demo-caption.show { opacity: 1; }
    #demo-caption b { color: #F2A541; font-weight: 700; }`;
  document.head.appendChild(css);
  const cur = Object.assign(document.createElement('div'), { id: 'demo-cursor' });
  const cap = Object.assign(document.createElement('div'), { id: 'demo-caption' });
  document.body.append(cur, cap);
  window.__demo = {
    move: (x, y) => { cur.style.left = x + 'px'; cur.style.top = y + 'px'; },
    down: (on) => cur.classList.toggle('down', on),
    caption: (html) => { if (!html) { cap.classList.remove('show'); return; }
      cap.classList.remove('show'); setTimeout(() => { cap.innerHTML = html; cap.classList.add('show'); }, 200); },
  };
})();
"""


class Demo:
    def __init__(self, page: Page):
        self.page = page
        self.pos = (720, 450)

    def overlay(self) -> None:
        self.page.evaluate(OVERLAY_JS)
        self.page.evaluate("([x, y]) => __demo.move(x, y)", list(self.pos))

    def wait(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def caption(self, html: str | None, hold: float = 0.0) -> None:
        self.page.evaluate("(h) => __demo.caption(h)", html)
        if hold:
            self.wait(hold)

    def move(self, x: float, y: float, duration: float = 0.6) -> None:
        steps = max(8, int(duration * 40))
        x0, y0 = self.pos
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)  # ease in-out
            cx, cy = x0 + (x - x0) * e, y0 + (y - y0) * e
            self.page.mouse.move(cx, cy)
            self.page.evaluate("([x, y]) => __demo.move(x, y)", [cx, cy])
            self.page.wait_for_timeout(int(duration * 1000 / steps))
        self.pos = (x, y)

    def click(self, selector: str, pause: float = 0.6) -> None:
        loc = self.page.locator(selector).first
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        assert box, f"not visible: {selector}"
        self.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        self.page.evaluate("__demo.down(true)")
        self.page.mouse.click(*self.pos)
        self.page.wait_for_timeout(120)
        self.page.evaluate("__demo.down(false)")
        self.wait(pause)

    def slide_time(self, to_minutes: int, step: int = 30, delay: float = 0.45) -> None:
        """Drag the departure slider visibly, re-scoring along the way."""
        slider = self.page.locator("#timeSlider")
        box = slider.bounding_box()
        current = int(slider.input_value())
        direction = 1 if to_minutes > current else -1
        values = list(range(current, to_minutes, direction * step)) + [to_minutes]
        for v in values:
            x = box["x"] + 8 + (box["width"] - 16) * v / int(slider.get_attribute("max"))
            self.move(x, box["y"] + box["height"] / 2, duration=0.15)
            self.page.evaluate("(v) => { const s = document.querySelector('#timeSlider'); s.value = v;"
                               " s.dispatchEvent(new Event('input', { bubbles: true })); }", v)
            self.page.wait_for_timeout(int(delay * 1000))
        self.wait_for_plan()

    def wait_for_plan(self, extra: float = 0.8) -> None:
        self.page.wait_for_function("() => typeof state !== 'undefined' && state.plan && !document.querySelector('.skeleton')")
        self.page.wait_for_timeout(int(extra * 1000))

    def scroll_panel(self, dy: int, steps: int = 12) -> None:
        for _ in range(steps):
            self.page.evaluate("(d) => document.querySelector('#view').scrollBy(0, d)", dy / steps)
            self.page.wait_for_timeout(35)


def hhmm_to_min(v: str) -> int:
    h, m = (int(x) for x in v.split(":"))
    return h * 60 + m


def fmt_clock(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{(h + 11) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


@dataclass
class Story:
    """Everything the captions say, read from the running API so they always match the app."""
    base: str
    city: dict
    origin: dict
    dest: dict
    night_min: int
    day_min: int
    late_min: int
    lamps_city: dict | None
    lamp_center: tuple[float, float] | None
    lamp_count: int
    helplines: list[dict]

    def url(self) -> str:
        f = lambda p: f"{p['lat']:.5f},{p['lon']:.5f},{quote(p['name'])}"  # noqa: E731
        return f"{self.base}/#" + urlencode({"city": self.city["id"], "o": f(self.origin), "d": f(self.dest),
                                             "t": self.night_min})

    def compare(self, minutes: int) -> tuple[dict, dict]:
        h, m = divmod(minutes, 60)
        plan = httpx.post(f"{self.base}/api/plan", timeout=60, json={
            "origin": {"lat": self.origin["lat"], "lon": self.origin["lon"]},
            "destination": {"lat": self.dest["lat"], "lon": self.dest["lon"]},
            "depart_at": f"2026-10-13T{h:02d}:{m:02d}:00"}).raise_for_status().json()
        best = plan["routes"][0]
        return best, next((r for r in plan["routes"] if r["label"] == "Shortest"), best)


def build_story(base: str, night: str | None, day: str, late: str) -> Story:
    get = lambda path: httpx.get(f"{base}{path}", timeout=60).raise_for_status().json()  # noqa: E731
    meta, cities = get("/api/meta"), get("/api/cities")
    city = next(c for c in cities if c["id"] == meta["default_city"])
    by_name = {lm["name"]: lm for lm in city["landmarks"]}
    trip = city.get("default_trip") or [lm["name"] for lm in city["landmarks"][:2]]
    lamps_city, lamp_center, lamp_count = None, None, 0
    for c in cities:  # the city whose default trip has the most mapped street lamps gets the night-lamps scene
        lms = [lm for lm in c["landmarks"] if lm["name"] in (c.get("default_trip") or [])] or c["landmarks"][:2]
        lats, lons = [lm["lat"] for lm in lms], [lm["lon"] for lm in lms]
        box = f"south={min(lats) - .01}&west={min(lons) - .01}&north={max(lats) + .01}&east={max(lons) + .01}"
        lamps = [f["geometry"]["coordinates"] for f in get(f"/api/layers?{box}")["features"]
                 if f["properties"]["kind"] == "street_lamp"]
        if len(lamps) > lamp_count:
            lons, lats = sorted(x[0] for x in lamps), sorted(x[1] for x in lamps)
            lamps_city, lamp_center, lamp_count = c, (lats[len(lats) // 2], lons[len(lons) // 2]), len(lamps)
    night = night or meta["default_departure"] or "21:30"
    return Story(base, city, by_name[trip[0]], by_name[trip[1]], hhmm_to_min(night), hhmm_to_min(day),
                 hhmm_to_min(late), lamps_city, lamp_center, lamp_count, meta["helplines"])


def run(demo: Demo, story: Story) -> None:
    page = demo.page
    best, short = story.compare(story.night_min)

    # 1 — default city at night
    page.goto(story.url())
    demo.overlay()
    demo.wait_for_plan(extra=3.5)  # let basemap tiles finish
    demo.caption("<b>SafeHer</b> compares walking routes on street lighting, open places, transit and more", 4.5)
    demo.caption(f"{story.origin['name']} → {story.dest['name']}, {story.city['name']}, "
                 f"at <b>{fmt_clock(story.night_min)}</b>", 3.5)
    demo.move(200, 236)
    if best is not short:
        extra = best["duration_min"] - short["duration_min"]
        longer = f"{extra} minute{'s' if extra != 1 else ''} longer" if extra > 0 else "about as quick"
        demo.caption(f"The <b>{best['label']}</b> route is {longer} but scores "
                     f"<b>{best['score']} vs {short['score']}</b>", 4.5)
    else:
        demo.caption(f"Here the shortest route also scores highest: <b>{best['score']}</b>", 4.5)

    # 2 — why this score
    demo.scroll_panel(330)
    demo.move(180, 520)
    demo.caption("Every score is explainable: five signals, each with how <b>confident</b> the data is", 5)
    demo.scroll_panel(-330)

    # 3 — weak stretches on the shortest route (only if it has any)
    if short["weak_stretches"] and best is not short:
        demo.click(".route-card:has-text('Shortest')", pause=1.5)
        demo.caption("The shortest route has <b>weaker stretches</b>. Tap one to see it", 2)
        demo.click("[data-weak='0']", pause=0.5)
        demo.wait(3)
        demo.click(".route-card >> nth=0", pause=1.5)

    # 4 — time of day
    day_best, day_short = story.compare(story.day_min)
    late_best, late_short = story.compare(story.late_min)
    demo.caption(f"Scores change with the <b>time of day</b>. Drag to {fmt_clock(story.day_min)}…", 1.5)
    demo.slide_time(story.day_min)
    demo.caption(f"Daytime: <b>{day_best['score']} vs {day_short['score']}</b>. "
                 "Lighting matters less and more places are open", 3.5)
    demo.caption(f"…and to <b>{fmt_clock(story.late_min)}</b>, when most shops and cafés have closed", 1.5)
    demo.slide_time(story.late_min)
    demo.caption(f"Late night: <b>{late_best['score']} vs {late_short['score']}</b>", 3)
    demo.slide_time(story.night_min, step=45, delay=0.3)
    demo.caption(None)

    # 5 — safe stops
    demo.click(".chip[data-kind='police']", pause=0.3)
    demo.caption("Nearest <b>police station</b>, washroom or open place: one tap", 4)
    demo.click(".chip[data-kind='toilets']", pause=0.3)
    demo.wait(3)
    demo.click("#backBtn", pause=1.2)

    # 6 — transparency
    demo.click("#explainBtn", pause=0.5)
    demo.caption("Weights, formula and limits are <b>published in the app</b>, never a black box", 3)
    page.evaluate("document.querySelector('#explainDialog').scrollBy({ top: 420, behavior: 'smooth' })")
    demo.wait(3)
    page.keyboard.press("Escape")
    demo.caption(None, 0.6)

    # 7 — street lamps (in whichever city has the most mapped around its default trip)
    if story.lamps_city:
        if story.lamps_city["id"] != story.city["id"]:
            demo.click(f"#citySwitch button[data-id='{story.lamps_city['id']}']", pause=0.2)
        demo.caption(f"In <b>{story.lamps_city['name']}</b>, {story.lamp_count} mapped street lamps glow on the night map", 0)
        demo.wait_for_plan(extra=3.5)
        lat, lon = story.lamp_center
        page.evaluate(f"map.easeTo({{ center: [{lon}, {lat}], zoom: 15.3, duration: 2500 }})")
        demo.wait(4)
        demo.caption("Lit stretches of the route glow amber, and only where lighting is <b>measured</b>, not guessed", 4.5)

    # 8 — reporting (not submitted)
    demo.click("#reportBtn", pause=0.5)
    demo.caption("Anyone can report a broken light or harassment, <b>anonymously</b>", 3.5)
    demo.click(".cat[data-cat='broken_light']", pause=0.3)
    demo.caption("Tap the map where it is. Routes re-score straight away", 3)
    demo.click("#bannerCancel", pause=0.6)

    # 9 — SOS
    demo.click("#sosBtn", pause=0.5)
    numbers = ", ".join(h["number"] for h in story.helplines[1:])
    demo.caption(f"Emergency numbers are always one tap away: <b>{story.helplines[0]['number']}</b>, {numbers}", 3.5)
    page.keyboard.press("Escape")
    demo.caption("<b>SafeHer</b>: safer decisions, not guarantees. Built on open data for the Google AI Builder Cup", 5)


def to_mp4(webm: Path) -> Path | None:
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    mp4 = webm.with_suffix(".mp4")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(webm),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(mp4)],
                   check=True)
    return mp4


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--base", default=DEFAULT_BASE, help="SafeHer server URL (env SAFEHER_URL)")
    ap.add_argument("--night", help="night departure HH:MM (default: config/app.json default_departure)")
    ap.add_argument("--day", default="13:00", help="daytime departure for the time-of-day scene")
    ap.add_argument("--late", default="23:45", help="late-night departure for the time-of-day scene")
    args = ap.parse_args()
    story = build_story(args.base.rstrip("/"), args.night, args.day, args.late)
    OUT_DIR.mkdir(exist_ok=True)
    tmp = OUT_DIR / "_raw"
    shutil.rmtree(tmp, ignore_errors=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed,
                                    args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = browser.new_context(viewport=SIZE, device_scale_factor=1, record_video_dir=str(tmp),
                                  record_video_size=SIZE, timezone_id=story.city["timezone"], locale="en-IN")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        started = time.time()
        try:
            run(Demo(page), story)
        finally:
            video = page.video
            ctx.close()
            browser.close()
        raw = Path(video.path())
        webm = OUT_DIR / "safeher-demo.webm"
        shutil.move(raw, webm)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"recorded {time.time() - started:.0f}s → {webm}")
    if errors:
        print("page errors:", *errors, sep="\n  ")
    mp4 = to_mp4(webm)
    if mp4:
        print(f"mp4 → {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
