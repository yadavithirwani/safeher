"""Record a captioned walkthrough of the SafeHer demo with Playwright.

Usage (server must be running on :8765):
    backend/.venv/bin/python demo/record_demo.py            # → demo/out/safeher-demo.mp4
    backend/.venv/bin/python demo/record_demo.py --headed   # watch it while it records

Captions and a visible cursor are injected into the page, because Playwright's video doesn't
show the real mouse. The script never submits a report, so it leaves no data behind.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://localhost:8765"
OUT_DIR = Path(__file__).resolve().parent / "out"
SIZE = {"width": 1440, "height": 900}
DELHI = "#area=del-hauz-khas-saket&o=28.54330,77.20670,Hauz%2520Khas%2520Metro&d=28.52860,77.21950,Select%2520Citywalk%252C%2520Saket&t=1290"

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
            x = box["x"] + 8 + (box["width"] - 16) * v / 1425
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


def run(demo: Demo) -> None:
    page = demo.page

    # 1 — Delhi at night
    page.goto(f"{BASE}/{DELHI}")
    demo.overlay()
    demo.wait_for_plan(extra=3.5)  # let basemap tiles finish
    demo.caption("<b>SafeHer</b> compares walking routes on street lighting, open places, transit and more", 4.5)
    demo.caption("Hauz Khas Metro → Select Citywalk, Delhi, at <b>9:30 PM</b>", 3.5)
    demo.move(200, 236)
    demo.caption("The <b>Most support</b> route is 1 minute longer but scores <b>77 vs 67</b>", 4.5)

    # 2 — why this score
    demo.scroll_panel(330)
    demo.move(180, 520)
    demo.caption("Every score is explainable: five signals, each with how <b>confident</b> the data is", 5)
    demo.scroll_panel(-330)

    # 3 — weak stretches on the shortest route
    demo.click(".route-card:has-text('Shortest')", pause=1.5)
    demo.caption("The shortest route has <b>weaker stretches</b>: little lighting data and few open places", 2)
    demo.click("[data-weak='0']", pause=0.5)
    demo.wait(3)
    demo.click(".route-card >> nth=0", pause=1.5)

    # 4 — time of day
    demo.caption("Scores change with the <b>time of day</b>. Drag to 1 PM…", 1.5)
    demo.slide_time(780)
    demo.caption("Daytime: lighting matters less and more places are open", 3.5)
    demo.caption("…and to <b>11:45 PM</b>, when most shops and cafés have closed", 1.5)
    demo.slide_time(1425)
    demo.wait(3)
    demo.slide_time(1290, step=45, delay=0.3)
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

    # 7 — Bengaluru street lamps
    demo.click("#areaSwitch button[data-id='blr-indiranagar-mg-road']", pause=0.2)
    demo.caption("In <b>Bengaluru</b>, 589 mapped street lamps glow on the night map", 0)
    demo.wait_for_plan(extra=3.5)
    page.evaluate("map.easeTo({ center: [77.6385, 12.9765], zoom: 15.3, duration: 2500 })")
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
    demo.caption("Emergency numbers are always one tap away: <b>112</b>, 181, 1091", 3.5)
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
    args = ap.parse_args()
    OUT_DIR.mkdir(exist_ok=True)
    tmp = OUT_DIR / "_raw"
    shutil.rmtree(tmp, ignore_errors=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed,
                                    args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = browser.new_context(viewport=SIZE, device_scale_factor=1, record_video_dir=str(tmp),
                                  record_video_size=SIZE, timezone_id="Asia/Kolkata", locale="en-IN")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        started = time.time()
        try:
            run(Demo(page))
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
