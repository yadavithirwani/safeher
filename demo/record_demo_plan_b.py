"""Append a real Plan B UI walkthrough to the live-sharing demo; preserve previous videos.

From repo root: .venv/bin/python demo/record_demo_plan_b.py [--headed]
Requires localhost:8765, Chrome, ffmpeg, and cached MapLibre assets (demo/README.md).
No geolocation permission, fabricated API responses, or sharing sessions are used.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from offline_map import configure, style
from record_demo import BASE, DELHI, SIZE, Demo

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
NAME = "safeher-demo-plan-b-and-live-sharing"


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    source = MEDIA / "safeher-demo-live-sharing.mp4"
    output = MEDIA / f"{NAME}.mp4"
    poster = MEDIA / f"{NAME}-poster.jpg"
    if not ffmpeg or not source.exists():
        raise SystemExit("Requires ffmpeg and the previous live-sharing demo.")
    if output.exists() or poster.exists():
        raise SystemExit("Outputs already exist; change NAME rather than overwrite a demo.")
    cache = ROOT / "demo" / "out" / "assets"
    if not all((cache / name).exists() for name in ("maplibre.js", "maplibre.css")):
        raise SystemExit("Cache MapLibre assets first; see demo/README.md.")
    map_style = style()
    with tempfile.TemporaryDirectory(prefix="safeher-plan-b-") as temp, sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        context = browser.new_context(viewport=SIZE, device_scale_factor=1,
            record_video_dir=temp, record_video_size=SIZE, timezone_id="Asia/Kolkata", locale="en-IN")
        configure(context, cache, map_style)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        video = page.video
        try:
            chapter(Demo(page))
            if errors:
                raise RuntimeError("Browser errors: " + "; ".join(errors))
        finally:
            context.close()
            browser.close()
        raw = Path(video.path())
        staged = Path(temp) / "updated.mp4"
        subprocess.run([ffmpeg, "-n", "-loglevel", "error", "-i", str(source), "-i", str(raw),
            "-filter_complex", "[0:v]scale=1440:900,setsar=1,fps=25[a];"
            "[1:v]scale=1440:900,setsar=1,fps=25[b];[a][b]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            "-movflags", "+faststart", str(staged)], check=True)
        # Poster from the new chapter, not old footage.
        staged_poster = Path(temp) / "poster.jpg"
        subprocess.run([ffmpeg, "-n", "-loglevel", "error", "-ss", "14", "-i", str(raw),
            "-frames:v", "1", "-update", "1", str(staged_poster)], check=True)
        shutil.copyfile(staged, output)
        shutil.copyfile(staged_poster, poster)
    print(f"Updated demo: {output} ({output.stat().st_size / 1e6:.1f} MB)")
    print(f"Poster: {poster}")


if __name__ == "__main__":
    main()
