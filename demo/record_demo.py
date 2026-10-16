"""Record the current SafeHer walkthrough, including real Ollama HerSide Q&A.

From repository root: .venv/bin/python demo/record_demo.py --base http://localhost:8767
Uses cached OSM rendering and simulated sharing GPS, both visibly labelled.
Chat, routing and sharing API responses are real. No reports are submitted.
"""
from __future__ import annotations

import argparse
import shutil
import networkx as nx
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
BASE = "http://localhost:8767"
SIZE = {"width": 1440, "height": 900}
MEDIA = ROOT / "docs" / "media"
NAME = "safeher-demo-herside"
GPS = {"latitude": 28.5433, "longitude": 77.2067, "accuracy": 5}
QUESTION = "Find the nearest pharmacy to these confirmed coordinates: 28.5433, 77.2067."


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
        self.page.evaluate("""() => {
          const badge = document.createElement('div');
          badge.textContent = 'DEMO · CACHED OSM MAP';
          badge.style.cssText = 'position:fixed;right:20px;top:16px;z-index:99999;'
            + 'background:white;color:#5b2a86;padding:8px 12px;border-radius:8px;font:bold 14px system-ui';
          document.body.append(badge);
        }""")
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

def style():
    from safeher.routing import load_area

    graph = nx.compose_all([
        load_area(area).graph
        for area in ('del-hauz-khas-saket', 'blr-indiranagar-mg-road')
    ])
    features = []
    for u, v, edge in graph.edges(data=True):
        if u > v:
            continue
        geometry = edge.get("geometry")
        coords = list(geometry.coords) if geometry is not None else [
            [graph.nodes[u]["x"], graph.nodes[u]["y"]],
            [graph.nodes[v]["x"], graph.nodes[v]["y"]],
        ]
        features.append({"type": "Feature", "properties": {},
                         "geometry": {"type": "LineString", "coordinates": coords}})
    return {"version": 8, "sources": {"streets": {"type": "geojson",
        "attribution": "© OpenStreetMap contributors · cached demo streets",
        "data": {"type": "FeatureCollection", "features": features}}}, "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": "#f4f0e8"}},
        {"id": "street-outline", "type": "line", "source": "streets",
         "paint": {"line-color": "#c6c1b7", "line-width": 6}},
        {"id": "street", "type": "line", "source": "streets",
         "paint": {"line-color": "#ffffff", "line-width": 3}},
    ]}

def configure(context, cache: Path, map_style):
    context.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    context.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    context.route("https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.js",
                  lambda route: route.fulfill(path=str(cache / "maplibre.js"), content_type="application/javascript"))
    context.route("https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.css",
                  lambda route: route.fulfill(path=str(cache / "maplibre.css"), content_type="text/css"))
    context.route("https://basemaps.cartocdn.com/**",
                  lambda route: route.fulfill(json=map_style))

def label_simulation(page):
    page.evaluate("""() => {
      const badge = document.createElement('div');
      badge.textContent = 'DEMO · SIMULATED GPS · CACHED OSM MAP';
      badge.style.cssText = 'position:fixed;right:20px;top:16px;z-index:999999;'
        + 'background:#fff;color:#5b2a86;border:2px solid #5b2a86;border-radius:8px;'
        + 'padding:8px 12px;font:bold 14px system-ui;pointer-events:none';
      document.body.append(badge);
      document.querySelector('#demo-caption').style.marginLeft = '0';
    }""")

def live_chapter(demo, browser, sessions, cache, map_style):
    page = demo.page
    page.goto(f"{BASE}/{DELHI}", wait_until="domcontentloaded")
    demo.overlay()
    label_simulation(page)
    demo.wait_for_plan(extra=2)
    demo.caption("New: <b>opt-in live location sharing</b> with people you trust. GPS is simulated in this demo.", 4)
    demo.click("#sharingBtn")
    # Captions must be inside the top-layer dialog to remain visible over its backdrop.
    page.evaluate("document.querySelector('#sharingDialog').append(document.querySelector('#demo-caption'))")
    demo.caption("Share only when you choose. Anyone with the viewer link can see your location and route.", 4)
    demo.caption("<b>Keep this page open</b>: updates may pause when the phone locks. Links expire after two hours.", 4)
    demo.click("#startSharing", pause=1)
    page.wait_for_function("document.querySelector('#sharingLink').value.includes('/live#')")
    session = page.evaluate("JSON.parse(sessionStorage.getItem('safeher-sharing'))")
    sessions.append(session)
    viewer_url = page.locator("#sharingLink").input_value()
    demo.caption("A private viewer link is ready. Only the latest GPS point is stored—<b>no location history</b>.", 4)
    demo.click("#copySharing")
    demo.caption("Send this link to a trusted contact. No app installation is needed to view it.", 3)

    # A non-recorded companion sender keeps real watchPosition updates flowing while
    # the recorded page navigates to the recipient view. GPS stays Playwright-mocked.
    sender_ctx = browser.new_context(viewport=SIZE, geolocation=GPS,
                                     permissions=["geolocation"], locale="en-IN")
    configure(sender_ctx, cache, map_style)
    try:
        sender = sender_ctx.new_page()
        sender.goto(BASE, wait_until="domcontentloaded")
        sender.evaluate("s => sessionStorage.setItem('safeher-sharing', JSON.stringify(s))", session)
        sender.reload(wait_until="domcontentloaded")
        sender.locator("#sharingBtn").click()
        sender.get_by_role("button", name="Resume location updates").click()
        page.goto(viewer_url, wait_until="domcontentloaded")
        demo.overlay()
        label_simulation(page)
        page.wait_for_function("document.querySelector('#liveStatus').textContent.includes('GPS accuracy')")
        demo.caption("The contact sees the route, latest position, <b>GPS accuracy</b>, and time since the last update.", 5)
        demo.click("#followLocation")
        points = [(28.5428, 77.2073), (28.5418, 77.2082), (28.5408, 77.2090)]
        demo.caption("Simulated movement: the sender’s GPS updates, and the viewer map follows on its next refresh.")
        for lat, lon in points:
            sender_ctx.set_geolocation({"latitude": lat, "longitude": lon, "accuracy": 7})
            demo.wait(7)
            demo.click("#followLocation", pause=1)
        demo.caption("Old updates are marked <b>STALE</b> after 30 seconds. A frozen dot is not proof of a live connection.", 5)
        page.goto(f"{BASE}/{DELHI}")
        demo.overlay()
        label_simulation(page)
        demo.wait_for_plan()
        demo.click("#sharingBtn")
        page.evaluate("document.querySelector('#sharingDialog').append(document.querySelector('#demo-caption'))")
        demo.caption("Back on the sender’s screen: <b>Stop sharing</b> revokes the link and deletes the stored position.", 3)
        demo.click("#stopSharing", pause=1)
        page.wait_for_function("document.querySelector('#sharingStatus').textContent.includes('Sharing stopped')")
        demo.wait(3)
        page.goto(viewer_url)
        demo.overlay()
        label_simulation(page)
        page.wait_for_function("document.querySelector('#liveStatus').textContent.includes('Sharing stopped')")
        demo.caption("The contact’s link no longer shows a location. <b>You control when sharing ends.</b>", 4)
        demo.caption("<b>SafeHer</b>: better-informed routes + opt-in sharing. Not a safety guarantee; in emergencies call 112.", 5)
    finally:
        sender_ctx.close()

def chapter(demo):
    page = demo.page
    page.goto(f"{BASE}/{DELHI}", wait_until="domcontentloaded")
    demo.overlay()
    page.evaluate("""() => {
      document.querySelector('#demo-caption').style.marginLeft = '204px';
      const badge = document.createElement('div');
      badge.textContent = 'PLAN B DEMO · CACHED OSM MAP';
      badge.style.cssText = 'position:fixed;right:20px;top:16px;z-index:999999;'
        + 'background:#fff;color:#5b2a86;border:2px solid #5b2a86;border-radius:8px;'
        + 'padding:8px 12px;font:bold 14px system-ui;pointer-events:none';
      document.body.append(badge);
    }""")
    demo.wait_for_plan(extra=2)
    demo.caption("New: <b>Plan B Coverage</b>. If you need to stop or ask for help, how far is a mapped option?", 5)
    page.locator(".backup").scroll_into_view_if_needed()
    demo.caption("Each route now shows the <b>longest estimated walk</b> to a potentially available help point.", 5)
    demo.caption("This uses walking streets, not a nearby circle. It is <b>separate from the route score</b>.", 5)
    demo.click(".backup summary")
    page.locator(".backup details").scroll_into_view_if_needed()
    demo.caption("Opening hours and entrances are uncertain. Mapped businesses are <b>not guaranteed refuges</b>.", 5)
    demo.click(".backup summary")
    demo.caption("At <b>11:45 PM</b>, fewer places are expected to remain open. Let’s compare the routes.", 3)
    demo.slide_time(1425, step=45, delay=.25)
    demo.click(".route-card:has-text('Shortest')", pause=1)
    page.locator(".backup").scroll_into_view_if_needed()
    coverage = page.evaluate("state.plan.routes[state.selected].plan_b")
    if not coverage["gaps"]:
        raise RuntimeError("Expected a real late-night coverage gap in the cached Delhi trip")
    demo.caption(f"Shortest route: longest sampled walk is about <b>{coverage['max_walk_min']} minutes</b>; "
                 f"{round(coverage['within_threshold_share'] * 100)}% is within five minutes.", 5)
    demo.click("[data-backup-gap='0']", pause=1)
    demo.caption("Tap a <b>coverage gap</b> to see the stretch beyond a five-minute walk—or without a mapped option.", 5)
    demo.click("[data-backup-worst]", pause=1)
    demo.caption("Focus on the <b>least-covered measured point</b>. This is a planning estimate, not turn-by-turn guidance.", 5)
    demo.click(".route-card:has-text('Alternative')", pause=1)
    page.locator(".backup").scroll_into_view_if_needed()
    coverage = page.evaluate("state.plan.routes[state.selected].plan_b")
    demo.caption(f"Alternative route: about <b>{coverage['max_walk_min']} minutes</b> at its least-covered sampled point. "
                 "Compare your options—not just distance.", 5)
    demo.click("[data-backup-gap='0']", pause=1)
    demo.caption("No mapped option is <b>missing coverage</b>, never proof that help does not exist.", 5)
    demo.click(".route-card >> nth=0", pause=1)
    page.locator(".backup").scroll_into_view_if_needed()
    demo.caption("<b>SafeHer</b>: route evidence, Plan B alternatives, and opt-in live sharing. No safety guarantees.", 6)


def routes_chapter(demo):
    page = demo.page
    page.goto(f"{BASE}/{DELHI}", wait_until="domcontentloaded")
    demo.overlay()
    demo.wait_for_plan(extra=2)
    demo.caption("<b>SafeHer</b> compares walking routes using lighting, transit, open places and community evidence.", 5)
    demo.caption("Delhi: Hauz Khas Metro → Select Citywalk at <b>9:30 PM</b>. Compare time, distance and support.", 5)
    demo.scroll_panel(300)
    demo.caption("Transparent signals include <b>data confidence</b>. These are support scores, not safety probabilities.", 5)
    demo.scroll_panel(-300)
    demo.click(".route-card:has-text('Shortest')")
    if page.locator("[data-weak='0']").count():
        demo.click("[data-weak='0']")
        demo.caption("Weak stretches show where supporting evidence is limited. No route is guaranteed safe.", 5)
    demo.slide_time(780, step=120, delay=.25)
    demo.caption("At <b>1 PM</b>, lighting carries less weight and more places are expected open.", 4)
    demo.slide_time(1425, step=120, delay=.25)
    demo.caption("At <b>11:45 PM</b>, hours and supporting evidence change. Typical hours are explicitly assumed.", 5)
    demo.slide_time(1290, step=90, delay=.2)
    demo.click(".chip[data-kind='police']")
    demo.caption("Nearby police, pharmacies and washrooms are available without chatting.", 4)
    demo.click("#backBtn")
    demo.click("#explainBtn")
    page.evaluate("document.querySelector('#explainDialog').append(document.querySelector('#demo-caption'))")
    demo.caption("The formula and limitations are visible in the app. <b>No black-box safety prediction.</b>", 5)
    page.keyboard.press("Escape")


def chat_chapter(demo, poster):
    page = demo.page
    page.goto(f"{BASE}/{DELHI}", wait_until="domcontentloaded")
    demo.overlay()
    demo.wait_for_plan()
    demo.click("#chatbotBtn")
    page.evaluate("""() => {
      const cap = document.querySelector('#demo-caption');
      document.querySelector('#chatbotDialog').append(cap);
      cap.style.marginLeft = '0'; cap.style.bottom = '18px';
    }""")
    demo.caption("Meet <b>HerSide</b>: a Google ADK agent team, running on local <b>Ollama llama3.2:3b</b>.", 5)
    demo.caption("Real Q&A: the agent chooses a search tool. The tool—not the model—supplies place facts.", 5)
    page.locator("#chatbotInput").fill(QUESTION)
    demo.caption("We provide demo coordinates explicitly. <b>No device location is collected</b> for this question.", 4)
    with page.expect_response(lambda response: response.url.endswith('/api/chat') and response.request.method == 'POST', timeout=90000) as pending:
        demo.click("#chatbotSend", pause=.2)
    response = pending.value
    body = response.json()
    assert response.ok and body['intent'] == 'local_enquiry', 'Chat did not route to the place-search specialist'
    assert body.get('places') and all(p.get('source') == 'google_places' for p in body['places']), 'Requires real Google results, not fixtures or fallback'
    page.locator(".herside-card").first.wait_for()
    demo.caption(None)
    demo.wait(4)
    page.screenshot(path=str(poster))
    demo.caption("Live <b>Google Places</b> results: names, source, listed hours and straight-line distance.", 5)
    page.locator("#chatbotMessages").evaluate('(el) => el.scrollTop = 0')
    demo.wait(3)
    page.locator("#chatbotMessages").evaluate('(el) => el.scrollTop = el.scrollHeight')
    demo.caption("Distances are not walking times. Listed hours do not verify availability or willingness to assist.", 5)
    demo.caption(None)
    demo.click("#chatbotSOS")
    page.get_by_role('link', name='India emergency: 112').wait_for(timeout=15000)
    demo.caption("<b>SOS bypasses the model</b>: India 112 remains available. No automatic calling or dispatch.", 5)
    demo.caption("Location is <b>opt-in and one-shot</b>. HerSide never starts live tracking automatically.", 5)
    demo.caption(None)
    page.keyboard.press('Escape')
    demo.caption("<b>SafeHer + HerSide</b>: transparent routes, Plan B, opt-in sharing and grounded assistance. Not a safety guarantee.", 6)


def main():
    global BASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=BASE)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    BASE = args.base.rstrip('/')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('ffmpeg is required')
    cache = ROOT / 'demo' / 'out' / 'assets'
    if not all((cache / name).exists() for name in ('maplibre.js', 'maplibre.css')):
        raise SystemExit('Cache MapLibre JS/CSS first; see demo/README.md')
    MEDIA.mkdir(exist_ok=True)
    map_style = style()
    sessions = []
    with tempfile.TemporaryDirectory(prefix='safeher-current-demo-') as temp, sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=not args.headed,
            args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'])
        context = browser.new_context(viewport=SIZE, device_scale_factor=1, record_video_dir=temp,
            record_video_size=SIZE, timezone_id='Asia/Kolkata', locale='en-IN',
            geolocation=GPS, permissions=['geolocation', 'clipboard-write'])
        configure(context, cache, map_style)
        page = context.new_page()
        page.set_default_timeout(60000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        video = page.video
        poster = Path(temp) / 'poster.jpg'
        try:
            demo = Demo(page)
            routes_chapter(demo)
            chapter(demo)
            live_chapter(demo, browser, sessions, cache, map_style)
            chat_chapter(demo, poster)
            if errors:
                raise RuntimeError('Browser JavaScript errors occurred during recording')
        finally:
            for session in sessions:
                result = context.request.delete(f"{BASE}/api/trips/{session['viewer_token']}",
                    headers={'Authorization': f"Bearer {session['owner_token']}"})
                if result.status not in (204, 404):
                    raise RuntimeError('Demo session revocation failed')
            context.close()
            browser.close()
        staged = Path(temp) / 'current.mp4'
        subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-i', video.path(), '-an', '-vf', 'fps=25',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', '-movflags', '+faststart', str(staged)], check=True)
        subprocess.run([ffmpeg, '-v', 'error', '-i', str(staged), '-f', 'null', '-'], check=True)
        shutil.copyfile(staged, MEDIA / f'{NAME}.mp4')
        shutil.copyfile(poster, MEDIA / f'{NAME}-poster.jpg')
    print(f"Verified recording: {MEDIA / f'{NAME}.mp4'}", flush=True)


if __name__ == '__main__':
    main()
