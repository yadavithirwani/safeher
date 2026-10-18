"""Record a fresh, silent SafeHer deployment walkthrough; never substitutes old footage."""
from __future__ import annotations
import json, re, shutil, subprocess, time, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, Page

BASE = "https://safeher-511808.web.app"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo/out/final" / f"capture-{time.strftime('%Y%m%d-%H%M%S')}"
SIZE = {"width": 1920, "height": 1080}
GPS = {"latitude": 12.9784, "longitude": 77.6408, "accuracy": 5}
QUESTION = "For this walk, is the extra walking time worth it for better lighting and fewer weak stretches?"
GROUND = "At Toit after 10pm, what safety support is available from staff, and is the venue open then?"


def plan(p: Page):
    """Wait for the real post-plan UI, never mistake boot skeletons for a plan."""
    p.wait_for_function("() => !document.querySelector('#bootScreen')", timeout=100000)
    p.wait_for_function("() => state?.plan?.routes?.length >= 1", timeout=100000)
    p.wait_for_selector("#view .route-card", timeout=100000)
    p.wait_for_timeout(1200)


def choose(p: Page, selector: str, text: str, area: str):
    p.locator(selector).fill(text)
    p.wait_for_timeout(900)
    p.locator("li[data-i]").filter(has_text=area).first.click()
    p.wait_for_timeout(1800)


def badge(p: Page, text="LIVE DEPLOYMENT"):
    # Keep the badge in the sharing dialog's top layer, rather than behind it.
    p.evaluate("""t => { const d=document.querySelector('#sharingDialog'); const b=document.createElement('div'); b.id='captureBadge'; b.textContent=t;
      b.style='position:absolute;right:18px;top:58px;z-index:2;background:#fff;color:#5b2a86;border:2px solid #5b2a86;border-radius:8px;padding:9px 14px;font:bold 15px system-ui;pointer-events:none'; d.append(b); }""", text)


def set_time(p: Page, value: int):
    """Drive the real slider and wait for the debounced latest plan, not a timer guess."""
    target = f"T{value // 60:02d}:{value % 60:02d}"
    p.locator("#timeSlider").evaluate("(s,v)=>{s.value=v;s.dispatchEvent(new Event('input',{bubbles:true}))}", value)
    p.wait_for_function("([v,t]) => state.isNow === false && state.departMin === v && String(state.plan?.departure_local || '').includes(t)", arg=[value, target], timeout=90000)
    p.wait_for_timeout(700)


def show_night_layer(p: Page):
    """Use the app's actual theme control so the VIIRS night-light layer is visible."""
    for _ in range(3):
        if "dark" in (p.locator("#themeBtn").get_attribute("aria-label") or ""):
            break
        p.locator("#themeBtn").click()
    p.wait_for_function("() => document.documentElement.dataset.theme === 'dark'", timeout=10000)
    p.wait_for_timeout(1200)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, sessions, provider = {}, [], None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport=SIZE, record_video_dir=str(OUT), record_video_size=SIZE,
                                  geolocation=GPS, permissions=["geolocation"], locale="en-IN",
                                  timezone_id="Asia/Kolkata")
        p = ctx.new_page(); p.set_default_timeout(60000)
        started = time.monotonic(); video = None; viewer_video = None
        sharing_windows = {}
        def begin(name):
            print(f"[scene:start] {name}", flush=True)
            manifest[name] = {"start_seconds": round(time.monotonic()-started, 2)}
        def useful(name, lead=1.0):
            manifest[name]["useful_start_seconds"] = max(0, round(time.monotonic()-started-lead, 2))
        def finish(name):
            manifest[name]["end_seconds"] = round(time.monotonic()-started, 2)
            manifest[name].setdefault("useful_start_seconds", manifest[name]["start_seconds"])
            manifest[name]["useful_end_seconds"] = manifest[name]["end_seconds"]
            try:
                shot = OUT / f"{name}.png"
                p.screenshot(path=str(shot), full_page=True)
                manifest[name]["screenshot"] = str(shot.relative_to(ROOT))
            except Exception as exc:
                manifest[name]["screenshot_error"] = repr(exc)
            print(f"[scene:done] {name}", flush=True)
            p.wait_for_timeout(1400)
        try:
            p.goto(BASE, wait_until="domcontentloaded"); plan(p)
            begin("routes_and_alternatives")
            p.get_by_role("tab", name="Bengaluru").click(); plan(p)
            choose(p, "#originInput", "Toit", "Old Binnamangala")
            choose(p, "#destInput", "Bob's Bar", "Doopanahalli")
            set_time(p, 1290)
            show_night_layer(p)
            if p.evaluate("state.plan.routes.length") < 2: raise RuntimeError("Deployment returned fewer than two route alternatives")
            finish("routes_and_alternatives")

            begin("time_dependent_lighting")
            set_time(p, 1080); set_time(p, 1290)
            show_night_layer(p)
            finish("time_dependent_lighting")

            begin("score_evidence_and_gemini_explanation")
            weak = p.locator("[data-weak]")
            if weak.count() > 0:
                weak.first.click(); p.wait_for_timeout(900)
            p.locator("#explainBtn").click(); p.wait_for_timeout(6500)
            # The deterministic breakdown dialog is separate from the AI explanation card.
            p.locator("#explainDialog").locator("button[aria-label='Close']").click()
            p.wait_for_function("() => document.querySelector('#aiCard')?.innerText.includes('Explained by Gemini')", timeout=90000)
            p.wait_for_function("() => document.querySelector('#aiCard')?.innerText.includes('Checked against this route')", timeout=90000)
            p.locator('#aiCard').scroll_into_view_if_needed()
            p.wait_for_timeout(6000)
            finish("score_evidence_and_gemini_explanation")

            begin("plan_b")
            p.locator(".backup").first.scroll_into_view_if_needed(); p.wait_for_timeout(1500)
            # Expand the actual Plan B disclosure if it is collapsed.
            backup = p.locator(".backup").first
            if backup.locator("details").count() and not backup.locator("details").first.get_attribute("open"):
                backup.locator("summary").first.click()
            p.wait_for_timeout(3000)
            finish("plan_b")

            begin("herside_pre_trip")
            p.locator("#chatbotBtn").click(); p.locator("#chatbotTrip").click()
            if p.locator("#chatbotInput").input_value() != QUESTION: raise RuntimeError("Trip attachment did not set required question")
            with p.expect_response(lambda r: r.url.endswith('/api/chat') and r.request.method == 'POST', timeout=90000) as wait:
                p.locator("#chatbotSend").click()
            response = wait.value; data = response.json()
            useful("herside_pre_trip")
            if not response.ok or data.get("intent") != "pre_trip": raise RuntimeError("Real HerSide pre-trip response was not returned")
            if len(data.get("routes", [])) < 2: raise RuntimeError("HerSide response lacked route alternatives")
            provider = p.locator("#chatbotModel").inner_text()
            p.wait_for_timeout(5000); finish("herside_pre_trip")

            begin("optional_maps_grounded_opening_hours")
            p.locator("#chatbotReset").click(); p.locator("#chatbotInput").fill(GROUND)
            with p.expect_response(lambda r: r.url.endswith('/api/chat') and r.request.method == 'POST', timeout=90000) as wait:
                p.locator("#chatbotSend").click()
            response = wait.value; data = response.json(); useful("optional_maps_grounded_opening_hours")
            sources = data.get("sources") or []
            if not response.ok or data.get("intent") != "local_enquiry" or not sources:
                manifest["optional_maps_grounded_opening_hours"] = {"skipped": True, "reason": "ChatResponse was not a grounded local enquiry", "intent": data.get("intent"), "sources": sources}
                shot = OUT / "optional_maps_grounded_opening_hours.png"
                p.screenshot(path=str(shot), full_page=True)
                manifest["optional_maps_grounded_opening_hours"]["screenshot"] = str(shot.relative_to(ROOT))
            else:
                p.wait_for_timeout(6000)
                finish("optional_maps_grounded_opening_hours")
            p.locator("#chatbotClose").click(); p.wait_for_timeout(500)

            begin("simulated_gps_location_sharing")
            sharing_windows["sender_start"] = time.monotonic() - started
            p.locator("#sharingBtn").click()
            # Mask before Start sharing populates the private URL; retain the real value for navigation.
            p.locator("#sharingLink").evaluate("e=>{e.style.color='transparent';e.style.textShadow='0 0 0 #5b2a86';e.style.webkitTextSecurity='disc'}")
            badge(p, "LIVE DEPLOYMENT · SIMULATED BROWSER GPS")
            p.locator("#startSharing").click()
            p.wait_for_function("() => document.querySelector('#sharingLink').value.includes('/live#')", timeout=30000)
            session = p.evaluate("JSON.parse(sessionStorage.getItem('safeher-sharing'))")
            sessions.append(session)
            link = p.locator("#sharingLink").input_value()
            p.locator("#sharingLink").evaluate("e=>{e.style.color='transparent';e.style.textShadow='0 0 0 #5b2a86';e.style.webkitTextSecurity='disc'}")
            viewer = ctx.new_page(); viewer.set_default_timeout(60000)
            viewer.goto(link, wait_until="domcontentloaded")
            viewer.wait_for_selector("#liveMap", timeout=60000)
            viewer.evaluate("""t => { const b=document.createElement('div'); b.textContent=t; b.style='position:fixed;right:18px;top:16px;z-index:999999;background:#fff;color:#5b2a86;border:2px solid #5b2a86;border-radius:8px;padding:9px 14px;font:bold 15px system-ui;pointer-events:none'; document.body.append(b); }""", "LIVE DEPLOYMENT · SIMULATED BROWSER GPS")
            viewer.wait_for_timeout(6000)
            sharing_windows["viewer_start"] = time.monotonic() - started
            p.wait_for_timeout(5000)
            ctx.set_geolocation({"latitude": 12.9792, "longitude": 77.6420, "accuracy": 6})
            p.wait_for_timeout(6000)
            # Keep recipient viewer visible while the sender performs the real stop/revocation.
            sharing_windows["sender_stop"] = time.monotonic() - started
            p.locator("#stopSharing").click(); p.wait_for_timeout(1800)
            if p.locator("#stopSharing").is_visible(): raise RuntimeError("Sharing UI did not stop/revoke session")
            sharing_windows["sender_end"] = time.monotonic() - started
            # Capture the viewer page independently; it is not included in sender p.video.
            viewer_video = viewer.video
            viewer.close()
            p.locator("#sharingDialog").locator("button[aria-label='Close']").click(); p.wait_for_timeout(500)
            finish("simulated_gps_location_sharing")

            begin("112_information_dialog")
            p.locator("#sosBtn").click(); p.wait_for_timeout(3500)
            finish("112_information_dialog")
            p.locator("#sosDialog").locator("button[aria-label='Close'], button").first.click(); p.wait_for_timeout(500)

            begin("delhi_coverage")
            p.get_by_role("tab", name="Delhi").click(); plan(p)
            p.wait_for_timeout(3500); finish("delhi_coverage")
            video = p.video
        except Exception as exc:
            manifest["_failure"] = {"error": repr(exc), "seconds": round(time.monotonic()-started, 2)}
            print(f"[capture:failure] {exc!r}", flush=True)
            try:
                p.screenshot(path=str(OUT / "failure.png"), full_page=True)
                print(f"[capture:failure-screenshot] {OUT / 'failure.png'}", flush=True)
            except Exception as shot_exc:
                manifest["_failure"]["screenshot_error"] = repr(shot_exc)
            (OUT / "manifest.failure.json").write_text(json.dumps({"deployment": BASE, "scenes": manifest}, indent=2))
            raise
        finally:
            failed = sys.exc_info()[0] is not None
            if failed:
                try:
                    p.screenshot(path=str(OUT / "failure.png"), full_page=True)
                except Exception:
                    pass
                (OUT / "manifest.failure.json").write_text(json.dumps({"deployment": BASE, "scenes": manifest}, indent=2))
            # Owner authorization is used for every session, including interrupted runs.
            for session in sessions:
                result = ctx.request.delete(f"{BASE}/api/trips/{session['viewer_token']}",
                    headers={"Authorization": f"Bearer {session['owner_token']}"})
                if result.status not in (204, 404): raise RuntimeError(f"Sharing revocation failed: HTTP {result.status}")
            ctx.close(); browser.close()
        if not video: raise RuntimeError("Browser produced no recording")
        full = OUT / "full.webm"; shutil.copy2(video.path(), full)
        viewer_path = None
        if viewer_video:
            viewer_path = OUT / "viewer.webm"
            shutil.copy2(viewer_video.path(), viewer_path)
        for key, item in manifest.items():
            if item.get("skipped"): continue
            start = item.get("useful_start_seconds", item["start_seconds"])
            end = item.get("useful_end_seconds", item["end_seconds"])
            clip = OUT / f"{key}.webm"
            if key == "simulated_gps_location_sharing" and viewer_path:
                # Accurate re-encoded cuts preserve sender start -> viewer movement -> sender stop order.
                sender_a = OUT / "_sharing_sender_start.webm"
                sender_b = OUT / "_sharing_sender_stop.webm"
                encode = ["-c:v", "libvpx-vp9", "-c:a", "libopus"]
                subprocess.run(["ffmpeg", "-y", "-i", str(full), "-ss", str(sharing_windows["sender_start"]), "-t", str(sharing_windows["viewer_start"]-sharing_windows["sender_start"]), *encode, str(sender_a)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["ffmpeg", "-y", "-i", str(full), "-ss", str(sharing_windows["sender_stop"]), "-t", str(sharing_windows["sender_end"]-sharing_windows["sender_stop"]), *encode, str(sender_b)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                concat = OUT / "_sharing_concat.txt"
                concat.write_text(f"file '{sender_a}'\nfile '{viewer_path}'\nfile '{sender_b}'\n")
                subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), *encode, str(clip)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(["ffmpeg", "-y", "-i", str(full), "-ss", str(start), "-t", str(end-start), "-c:v", "libvpx-vp9", "-c:a", "libopus", str(clip)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            item["clip"] = str(clip.relative_to(ROOT))
        result = {"deployment": BASE, "viewport": SIZE, "provider": provider, "chat_requests": 2,
                  "gps": "simulated browser geolocation; visibly labeled", "scenes": manifest}
        (OUT / "manifest.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
