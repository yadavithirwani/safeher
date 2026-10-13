"""Append a simulated live-sharing chapter to original footage without overwriting v1.

From the repo root: .venv/bin/python demo/record_demo_live.py [--headed]
Requires the server on localhost:8765, Chrome, and ffmpeg on PATH.
Uses only simulated GPS; revokes the demo session even if recording fails.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from record_demo import BASE, DELHI, SIZE, Demo
from offline_map import configure, style

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
NAME = "safeher-demo-live-sharing"
GPS = {"latitude": 28.5433, "longitude": 77.2067, "accuracy": 5}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--proxy", help="Optional browser proxy; localhost is bypassed")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("Install ffmpeg first; existing videos will not be changed.")
    mp4 = MEDIA / f"{NAME}.mp4"
    poster = MEDIA / f"{NAME}-poster.jpg"
    if mp4.exists() or poster.exists():
        raise SystemExit("Updated output already exists; choose a new NAME rather than overwrite it.")
    cache = ROOT / "demo" / "out" / "assets"
    if not all((cache / name).exists() for name in ("maplibre.js", "maplibre.css")):
        raise SystemExit("Cache MapLibre JS/CSS in demo/out/assets first (see demo/README.md).")
    map_style = style()
    sessions = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="safeher-live-demo-") as raw_dir, sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed,
            proxy={"server": args.proxy, "bypass": "localhost,127.0.0.1"} if args.proxy else None,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = browser.new_context(viewport=SIZE, device_scale_factor=1,
            record_video_dir=raw_dir, record_video_size=SIZE, timezone_id="Asia/Kolkata",
            locale="en-IN", geolocation=GPS, permissions=["geolocation", "clipboard-write"])
        configure(ctx, cache, map_style)
        page = ctx.new_page()
        page.set_default_navigation_timeout(90000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        video = page.video
        try:
            demo = Demo(page)
            live_chapter(demo, browser, sessions, cache, map_style)
            if errors:
                raise RuntimeError("Browser errors: " + "; ".join(errors))
        finally:
            for session in sessions:
                response = ctx.request.delete(f"{BASE}/api/trips/{session['viewer_token']}",
                    headers={"Authorization": f"Bearer {session['owner_token']}"})
                if response.status not in (204, 404):
                    print("WARNING: demo session revocation failed; it expires automatically.")
            ctx.close()
            browser.close()
        raw = Path(video.path())
        chapter = Path(raw_dir) / "chapter.mp4"
        subprocess.run([ffmpeg, "-n", "-loglevel", "error", "-i", str(raw),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            "-movflags", "+faststart", str(chapter)], check=True)
        subprocess.run([ffmpeg, "-n", "-loglevel", "error",
            "-i", str(MEDIA / "safeher-demo.mp4"), "-i", str(chapter),
            "-filter_complex", "[0:v]scale=1440:900,setsar=1,fps=25[a];"
            "[1:v]scale=1440:900,setsar=1,fps=25[b];[a][b]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            "-movflags", "+faststart", str(mp4)], check=True)
        subprocess.run([ffmpeg, "-n", "-loglevel", "error", "-ss", "8", "-i", str(mp4),
            "-frames:v", "1", "-update", "1", str(poster)], check=True)
    print(f"Recorded in {time.monotonic() - started:.0f}s: {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")
    print(f"Poster: {poster}")


if __name__ == "__main__":
    main()
