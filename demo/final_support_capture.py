"""Short real-deployment support capture for sharing + SOS only (no model calls)."""
from __future__ import annotations
import json, shutil, time
from pathlib import Path
from playwright.sync_api import sync_playwright, Page

BASE = "https://safeher-511808.web.app"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo/out/final" / f"support-{time.strftime('%Y%m%d-%H%M%S')}"
SIZE = {"width": 1920, "height": 1080}
GPS = {"latitude": 12.9784, "longitude": 77.6408, "accuracy": 5}
GPS2 = {"latitude": 12.9792, "longitude": 77.6420, "accuracy": 6}

INIT = r"""
(() => {
  const points = [{lat:12.9784,lon:77.6408,accuracy:5},{lat:12.9792,lon:77.6420,accuracy:6}];
  let n = 0, nextId = 1; const timers = new Map();
  const log = (kind, point) => { window.__safeherGeoLog = window.__safeherGeoLog || []; window.__safeherGeoLog.push({kind, now:Date.now(), timestamp:Date.now(), point}); };
  const position = () => { const x=points[Math.min(n++, points.length-1)]; const timestamp=Date.now(); const point={latitude:x.lat,longitude:x.lon,accuracy:x.accuracy}; log('synthetic', point); return {coords:point,timestamp}; };
  Object.defineProperty(navigator, 'geolocation', { configurable:false, value:{
    getCurrentPosition(ok, err) { const id=nextId++; const t=setTimeout(() => { timers.delete(id); ok(position()); }, 20); timers.set(id,t); },
    watchPosition(ok, err) { const id=nextId++; const tick=()=>{ if(!timers.has(id)) return; const t=setTimeout(()=>{ if(!timers.has(id)) return; ok(position()); tick(); },20); timers.set(id,t); }; timers.set(id,0); tick(); return id; },
    clearWatch(id) { const t=timers.get(id); if(t) clearTimeout(t); timers.delete(id); }
  }});
  const install = () => {
    if (!document.documentElement) return;
    const badge = () => {
      const target = document.querySelector('#sharingDialog[open]') || document.body;
      if (!target) return;
      let b = document.getElementById('supportGpsBadge');
      if (!b) { b=document.createElement('div'); b.id='supportGpsBadge'; b.textContent='SIMULATED GPS · SUPPORT CAPTURE'; }
      const inDialog = target.id === 'sharingDialog';
      if (b.parentElement !== target) target.append(b);
      b.style.cssText=`position:${inDialog?'absolute':'fixed'};right:18px;top:${inDialog?'58px':'16px'};z-index:2147483647;background:#fff;color:#5b2a86;border:2px solid #5b2a86;border-radius:8px;padding:9px 14px;font:bold 15px system-ui;pointer-events:none`;
    };
    const mask = () => {
      const input=document.querySelector('#sharingLink'); if(!input || input.dataset.maskReady) return;
      input.dataset.maskReady='1'; input.setAttribute('aria-label','Private viewer link (hidden in support capture)');
      input.style.opacity='0'; input.style.position='relative';
      const holder=input.parentElement; holder.style.position='relative';
      const cover=document.createElement('span'); cover.id='supportPrivateLink'; cover.textContent='PRIVATE LINK HIDDEN';
      cover.style='position:absolute;left:8px;top:8px;color:#5b2a86;font:600 14px system-ui;pointer-events:none'; holder.append(cover);
    };
    const refresh=()=>{ badge(); mask(); };
    new MutationObserver(refresh).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});
    refresh();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once:true}); else install();
})();
"""


def wait_ready(p: Page):
    p.wait_for_function("() => !document.querySelector('#bootScreen')", timeout=90000)
    p.wait_for_selector("#view .route-card", timeout=90000)
    p.wait_for_function("() => state?.plan?.routes?.length >= 1", timeout=90000)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"deployment": BASE, "started_at": time.time(), "gps_mode": "synthetic navigator geolocation with live API writes"}
    sessions = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport=SIZE, record_video_dir=str(OUT), record_video_size=SIZE,
                                  geolocation=GPS, permissions=["geolocation"], locale="en-IN", timezone_id="Asia/Kolkata")
        ctx.add_init_script(INIT)
        network_log = []
        sender = ctx.new_page(); sender.set_default_timeout(45000)
        capture_zero = time.monotonic()
        sender.on("requestfailed", lambda r: network_log.append({"url": r.url, "failed": r.failure}))
        sender.on("console", lambda m: network_log.append({"page":"sender", "console":m.type, "text":m.text}))
        viewer = None; sender_video = viewer_video = None
        try:
            sender.goto(BASE + '/#city=bengaluru&o=12.9784,77.6408,Toit&d=12.9700,77.6407,Bob%2527s%2520Bar&t=1290', wait_until="domcontentloaded"); wait_ready(sender)
            manifest["capture_clock"] = {"zero_monotonic": capture_zero, "sender_page_offset": 0.0}
            manifest['sharing_start_seconds'] = round(time.monotonic() - capture_zero, 3)
            sender.locator("#sharingBtn").click()
            sender.wait_for_function("() => document.querySelector('#supportGpsBadge')?.textContent.includes('SIMULATED GPS') && document.querySelector('#supportGpsBadge')?.parentElement?.id === 'sharingDialog'", timeout=10000)
            sender.wait_for_function("() => getComputedStyle(document.querySelector('#sharingLink')).opacity === '0' && document.querySelector('#supportPrivateLink')", timeout=10000)
            # INIT masks the field before Start sharing populates the real private value.
            # Require the real sender PUT to succeed before opening the recipient viewer.
            with sender.expect_response(lambda r: f"/api/trips/" in r.url and r.url.endswith("/location") and r.request.method == "PUT", timeout=30000) as put_wait:
                sender.locator("#startSharing").click()
            put_response = put_wait.value
            if not put_response.ok: raise RuntimeError(f"Initial location PUT failed: HTTP {put_response.status}")
            sender.wait_for_function("() => document.querySelector('#sharingLink').value.includes('/live#')", timeout=30000)
            session = sender.evaluate("JSON.parse(sessionStorage.getItem('safeher-sharing'))")
            if not session: raise RuntimeError("No sharing session was created")
            sessions.append(session)
            token = session["viewer_token"]
            link = sender.locator("#sharingLink").input_value()
            put = ctx.request.get(f"{BASE}/api/trips/{token}")
            first = put.json() if put.ok else {}
            manifest["initial_get"] = {"status": put.status, "has_location": bool(first.get("location"))}
            if not put.ok: raise RuntimeError(f"Viewer GET failed before opening viewer: HTTP {put.status}")

            sender.wait_for_timeout(5000)
            manifest["sender_active_hold_seconds"] = 5

            viewer = ctx.new_page(); viewer.set_default_timeout(45000)
            viewer_offset = time.monotonic() - capture_zero
            manifest["viewer_page_offset_seconds"] = round(viewer_offset, 3)
            viewer.on("requestfailed", lambda r: network_log.append({"page":"viewer", "url":r.url, "failed":r.failure}))
            viewer.on("console", lambda m: network_log.append({"page":"viewer", "console":m.type, "text":m.text}))
            viewer.goto(link, wait_until="domcontentloaded")
            viewer.wait_for_function("() => document.querySelector('#supportGpsBadge')?.textContent.includes('SIMULATED GPS')", timeout=10000)
            viewer.wait_for_selector("#liveMap", timeout=30000)
            ctx.set_geolocation(GPS2)
            viewer.wait_for_timeout(6000)
            manifest["viewer_fresh_hold_seconds"] = 6
            # The external basemap is intentionally not required; capture the real header/status only.
            viewer.wait_for_function("() => document.querySelector('#liveMap')?.clientWidth > 0 && document.querySelector('#liveMap')?.clientHeight > 0", timeout=10000)
            get = ctx.request.get(f"{BASE}/api/trips/{token}")
            payload = get.json() if get.ok else {}
            if not get.ok or not payload.get("location"):
                raise RuntimeError(f"Viewer never received a location: HTTP {get.status}")
            manifest["location_get"] = {"status": get.status, "location": payload["location"], "browser_now_ms": viewer.evaluate("Date.now()"), "location_updated_at_ms": int(payload["location"].get("updated_at", 0) * 1000)}
            manifest["geo_clock_log_sender"] = sender.evaluate("window.__safeherGeoLog || []")
            manifest["geo_clock_log_viewer"] = viewer.evaluate("window.__safeherGeoLog || []")
            sender.screenshot(path=str(OUT / "sharing-sender.png"), full_page=True)
            viewer.evaluate("() => { const b=document.querySelector('#supportGpsBadge'); if (b) b.textContent='SIMULATED GPS · BASEMAP UNAVAILABLE ON RECORDING NETWORK'; }")
            viewer.screenshot(path=str(OUT / "sharing-viewer.png"), full_page=True)
            viewer_video = viewer.video
            manifest["network_log"] = network_log
            manifest["viewer_map_available"] = False
            manifest["viewer_map_note"] = "Basemap unavailable on recording network; viewer header/status and live API state captured honestly."
            manifest["route_hash"] = sender.evaluate("location.href")
            manifest["sharing"] = {"successful": True, "sender_screenshot": "sharing-sender.png", "viewer_screenshot": "sharing-viewer.png", "viewer_label": "SIMULATED GPS · BASEMAP UNAVAILABLE ON RECORDING NETWORK"}

            manifest['sender_stop_seconds'] = round(time.monotonic() - capture_zero, 3)
            sender.locator("#sharingDialog").locator("#stopSharing").click()
            sender.wait_for_timeout(4000)
            manifest["sender_stop_hold_seconds"] = 4
            viewer.wait_for_function("() => /stopped|expired|unavailable/i.test(document.querySelector('#liveStatus')?.textContent || '')", timeout=30000)
            revoked = ctx.request.get(f"{BASE}/api/trips/{token}")
            manifest["revocation_get"] = {"status": revoked.status, "revoked": revoked.status == 404}
            if revoked.status != 404: raise RuntimeError("Sharing revocation did not produce viewer 404")
            viewer.close(); viewer = None
            sender.locator("#sharingDialog").locator("button[aria-label='Close']").click()

            manifest['sos_start_seconds'] = round(time.monotonic() - capture_zero, 3)
            sender.locator("#sosBtn").click()
            sender.wait_for_selector("#sosDialog[open]", timeout=10000)
            sender.wait_for_timeout(6000)
            manifest['sos_end_seconds'] = round(time.monotonic() - capture_zero, 3)
            sender.screenshot(path=str(OUT / "sos.png"), full_page=True)
            manifest["sos"] = {"successful": True, "screenshot": "sos.png", "hold_seconds": 6}
            sender.locator("#sosDialog").locator("button[aria-label='Close']").click()
            sender_video = sender.video
            manifest["status"] = "success"
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["error"] = repr(exc)
            manifest["network_log"] = network_log
            try: sender.screenshot(path=str(OUT / "failure.png"), full_page=True)
            except Exception: pass
            raise
        finally:
            manifest["finished_at"] = time.time()
            # Persist timings and GPS clock diagnostics before any video/export failure can erase them.
            (OUT / "support-manifest.json").write_text(json.dumps(manifest, indent=2))
            for session in sessions:
                result = ctx.request.delete(f"{BASE}/api/trips/{session['viewer_token']}", headers={"Authorization": f"Bearer {session['owner_token']}"})
                manifest.setdefault("cleanup", []).append({"status": result.status})
            if viewer: viewer.close()
            sender_video = sender.video or sender_video
            ctx.close(); browser.close()
        if sender_video:
            shutil.copy2(sender_video.path(), OUT / "sender.webm")
        if viewer_video:
            shutil.copy2(viewer_video.path(), OUT / "viewer.webm")
        manifest["raw_video"] = "sender.webm"
        (OUT / "support-manifest.json").write_text(json.dumps(manifest, indent=2))
        print(json.dumps({"output": str(OUT), **manifest}, indent=2), flush=True)


if __name__ == "__main__": main()
