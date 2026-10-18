"""Fresh live SafeHer capture. Existing media/scripts are never touched."""
from __future__ import annotations
import json, re, shutil, subprocess, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from final_support_capture import INIT

BASE = "https://safeher-511808.web.app"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo/out/updated" / f"capture-{time.strftime('%Y%m%d-%H%M%S')}"
SIZE = {"width": 1920, "height": 1080}
ORIGIN = "Mahakavi Kuvempu Road metro"
DEST = "Sri Vani Girls High School"
QUESTION = "Is the longer route worth 2 minutes?"
GROUND = "Which cafés near Sri Vani Girls High School stay open after 11 pm?"
REFUSAL = "Recommend a movie for tonight?"
GPS = {"latitude": 12.9784, "longitude": 77.6408, "accuracy": 5}
GPS2 = {"latitude": 12.9792, "longitude": 77.6420, "accuracy": 6}


def wait_plan(p):
    p.wait_for_function("() => !document.querySelector('#bootScreen')", timeout=110000)
    p.wait_for_function("() => state?.plan?.routes?.length >= 1", timeout=110000)
    p.wait_for_selector("#view .route-card", timeout=110000)
    p.wait_for_timeout(1200)


def pick(p, selector, text, contains):
    p.locator(selector).fill(text); p.wait_for_timeout(1000)
    items = p.locator("li[data-i]").filter(has_text=contains)
    if not items.count():
        items = p.locator("li[data-i]")
    if not items.count(): raise RuntimeError(f"No live place suggestion for {text}")
    items.first.click(); p.wait_for_timeout(2200)


def set_departure(p, minutes):
    p.locator("#timeSlider").evaluate("(s,v)=>{s.value=v;s.dispatchEvent(new Event('input',{bubbles:true}))}", minutes)
    target = f"T{minutes // 60:02d}:{minutes % 60:02d}"
    p.wait_for_function("([v,t]) => state.departMin === v && state.isNow === false && String(state.plan?.departure_local || '').includes(t)", arg=[minutes, target], timeout=90000)
    p.wait_for_timeout(1000)


def redact(value):
    if isinstance(value, str):
        value = re.sub(r'(Bearer|token|owner_token|viewer_token)[=:][^&\s,}]+', r'\1=[REDACTED]', value, flags=re.I)
        value = re.sub(r'/live#[^\s"\\]+', '/live#[REDACTED]', value)
        return value
    if isinstance(value, dict): return {k: redact(v) for k, v in value.items() if k not in {'owner_token','viewer_token','token','authorization','session_id'}}
    if isinstance(value, list): return [redact(v) for v in value]
    return value


def route_metrics(p):
    return p.evaluate("""() => (state.plan.routes || []).map((r,i)=>({index:i,id:r.id||null,label:r.label||null,
      duration_min:r.duration_min,distance_m:r.distance_m,score:r.score,band:r.band,
      lit_pct:r.lit_pct,open_places:r.open_places,weak_stretches:r.weak_stretches||[],plan_b:r.plan_b||null,
      breakdown:r.breakdown||[],evidence:r.evidence||null,raw_keys:Object.keys(r)}))""")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    scenes, evidence, sessions = {}, {"departure_date_requested":"17 Oct 2026"}, []
    t0 = time.monotonic(); provider = None; raw_video = None; viewer_video = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader","--enable-unsafe-swiftshader"])
        ctx = browser.new_context(viewport=SIZE, record_video_dir=str(OUT), record_video_size=SIZE,
            geolocation=GPS, permissions=["geolocation"], locale="en-IN", timezone_id="Asia/Kolkata")
        geo_init = INIT.replace('12.9784', '12.9985023').replace('77.6408', '77.5569472').replace('12.9792', '12.9990').replace('77.6420', '77.5565')
        ctx.add_init_script(geo_init)
        p = ctx.new_page(); p.set_default_timeout(60000)
        t0 = time.monotonic()
        def start(key): scenes[key] = {"start_seconds": round(time.monotonic()-t0,2)}
        def shot(key):
            print(f'[scene] {key}', flush=True)
            path=OUT/f"{key}.png"; p.screenshot(path=str(path),full_page=True)
            scenes[key]["end_seconds"]=round(time.monotonic()-t0,2); scenes[key]["screenshot"]=str(path.relative_to(ROOT))
        def do_chat(key, text, expected=None):
            nonlocal provider
            if key != 'pre_trip' and p.locator('#chatbotReset').count(): p.locator('#chatbotReset').click()
            p.locator("#chatbotInput").fill(text)
            with p.expect_response(lambda r:r.url.endswith('/api/chat') and r.request.method=='POST',timeout=100000) as w:
                p.locator("#chatbotSend").click()
            r=w.value; data=redact(r.json())
            evidence[key]={"http_status":r.status,"ok":r.ok,"intent":data.get("intent"),"provider":data.get("provider"),
                "grounding":data.get("grounding"),"sources":data.get("sources",[]),"answer":data.get("answer") or data.get("response"),
                "routes":data.get("routes"),"refusal":data.get("refusal")}
            provider = provider or data.get("provider")
            evidence[key]['full_response'] = data
            p.wait_for_timeout(4500); return data
        try:
            route_url = BASE + '/#city=bengaluru&o=12.9985023,77.5569472,Kuvempu%20Road%20Metro%20Station&d=13.004793,77.553319,Sri%20Vani%20Girls%20High%20School&t=1320'
            p.goto(route_url,wait_until="domcontentloaded"); wait_plan(p)
            p.wait_for_function("() => String(state.plan?.departure_local || '').includes('T22:00')",timeout=90000)
            evidence["origin_destination"] = p.evaluate("() => ({origin:state.origin,destination:state.dest,departure_local:state.plan?.departure_local})")
            evidence["10pm_routes"] = route_metrics(p)
            evidence["10pm_plan_snapshot"] = redact(p.evaluate("() => state.plan"))
            evidence["departure_date_support"] = {"requested":"17 Oct 2026", "supported":False, "note":"Live UI exposes departure time only; planner uses current Bengaluru date."}
            start("walk_home"); shot("walk_home")
            start("route_comparison"); p.locator(".route-card").first.scroll_into_view_if_needed(); shot("route_comparison")
            start("score_evidence");
            if p.locator("[data-weak]").count(): p.locator("[data-weak]").first.click()
            if p.locator("#explainBtn").count():
                p.locator("#explainBtn").click(); p.wait_for_timeout(4000)
                if p.locator("#explainDialog").count() and p.locator("#explainDialog").is_visible(): p.locator("#explainDialog button[aria-label='Close']").click()
            p.wait_for_function("() => document.querySelector('#aiCard')?.innerText.includes('Explained by Gemini') && document.querySelector('#aiCard')?.innerText.includes('Checked against this route')",timeout=100000)
            evidence["gemini_explanation_text"] = redact(p.locator("#aiCard").inner_text())
            p.locator('#aiCard').scroll_into_view_if_needed()
            shot("score_evidence")
            start("plan_b"); p.locator(".backup").first.scroll_into_view_if_needed(); p.wait_for_timeout(2000); shot("plan_b")
            p.locator('#timeSlider').scroll_into_view_if_needed()
            set_departure(p,1410) # 23:30; actual live app state is recorded below
            evidence["11pm_routes"] = route_metrics(p)
            evidence["11_30pm_plan_snapshot"] = redact(p.evaluate("() => state.plan"))
            start("late_night"); shot("late_night")
            set_departure(p,1320)
            start("herside_pre_trip"); p.locator("#chatbotBtn").click(); p.locator("#chatbotTrip").click()
            p.locator('#chatbotInput').fill(QUESTION)
            do_chat("pre_trip",QUESTION); shot("herside_pre_trip")
            start("maps_grounding"); do_chat("maps_grounding",GROUND); shot("maps_grounding")
            start("scope_guard"); do_chat("scope_guard",REFUSAL); shot("scope_guard"); p.locator("#chatbotClose").click()
            # Real sharing session, with mask installed before Start sharing and visible synthetic-GPS label.
            start("sharing"); p.locator("#sharingBtn").click()
            p.evaluate("""() => {const d=document.querySelector('#sharingDialog');const b=document.createElement('b');b.id='captureGpsBadge';b.textContent='SIMULATED GPS · LIVE DEPLOYMENT';b.style='position:absolute;right:18px;top:58px;z-index:9;background:white;color:#5b2a86;border:2px solid #5b2a86;padding:9px;border-radius:8px';d.append(b);const i=document.querySelector('#sharingLink');if(i){i.style.color='transparent';i.setAttribute('aria-label','Private viewer link hidden');}}""")
            with p.expect_response(lambda r:'/api/trips/' in r.url and r.url.endswith('/location') and r.request.method=='PUT',timeout=30000) as w:
                p.locator("#startSharing").click()
            if not w.value.ok: raise RuntimeError(f"real location PUT failed {w.value.status}")
            p.wait_for_function("() => document.querySelector('#sharingLink').value.includes('/live#')",timeout=30000)
            session=p.evaluate("JSON.parse(sessionStorage.getItem('safeher-sharing'))"); sessions.append(session)
            link=p.locator("#sharingLink").input_value()
            viewer=ctx.new_page(); viewer.set_default_timeout(50000); viewer.goto(link,wait_until='domcontentloaded'); viewer.wait_for_selector('#liveMap',timeout=50000)
            viewer.evaluate("""()=>{const b=document.createElement('b');b.textContent='SIMULATED GPS · BASEMAP STATUS ONLY';b.style='position:fixed;right:18px;top:16px;z-index:999;background:white;color:#5b2a86;border:2px solid #5b2a86;padding:9px';document.body.append(b)}""")
            viewer.wait_for_timeout(5000); ctx.set_geolocation(GPS2); viewer.wait_for_timeout(5000)
            get=ctx.request.get(f"{BASE}/api/trips/{session['viewer_token']}"); payload=redact(get.json() if get.ok else {})
            evidence["sharing_api"]={"initial_put_status":w.value.status,"viewer_get_status":get.status,"location":payload.get("location")}
            p.screenshot(path=str(OUT/'sharing-sender.png'),full_page=True); viewer.screenshot(path=str(OUT/'sharing-viewer.png'),full_page=True)
            viewer_video=viewer.video; scenes["sharing"].update({"sender_screenshot":str((OUT/'sharing-sender.png').relative_to(ROOT)),"viewer_screenshot":str((OUT/'sharing-viewer.png').relative_to(ROOT)),"viewer_map_note":"Basemap status only; no extended network troubleshooting."})
            p.locator("#stopSharing").click(); p.wait_for_timeout(2500); rev=ctx.request.get(f"{BASE}/api/trips/{session['viewer_token']}"); evidence["sharing_api"]["revocation_get_status"]=rev.status
            viewer.close(); p.locator("#sharingDialog button[aria-label='Close']").click(); scenes["sharing"]["end_seconds"]=round(time.monotonic()-t0,2)
            start("sos"); p.locator("#sosBtn").click(); p.wait_for_selector("#sosDialog[open]",timeout=10000); p.wait_for_timeout(3500); shot("sos")
            raw_video=p.video
        except Exception as exc:
            print(f'[capture failure] {exc!r}', flush=True)
            evidence["failure"]={"error":repr(exc),"seconds":round(time.monotonic()-t0,2)}
            p.screenshot(path=str(OUT/'failure.png'),full_page=True)
        finally:
            for s in sessions:
                try: ctx.request.delete(f"{BASE}/api/trips/{s['viewer_token']}",headers={"Authorization":f"Bearer {s['owner_token']}"})
                except Exception: pass
            raw_video=p.video or raw_video
            ctx.close()
            if raw_video:
                shutil.copy2(raw_video.path(), OUT/'._capture_source.webm')
            browser.close()
    if raw_video:
        full=OUT/'._capture_source.webm'
        for key,item in scenes.items():
            if 'start_seconds' not in item or 'end_seconds' not in item: continue
            clip=OUT/f'{key}.webm'; subprocess.run(['ffmpeg','-y','-i',str(full),'-ss',str(item['start_seconds']),'-t',str(max(.2,item['end_seconds']-item['start_seconds'])),'-c:v','libvpx-vp9','-c:a','libopus',str(clip)],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); item['clip']=str(clip.relative_to(ROOT))
        try: full.unlink()
        except FileNotFoundError: pass
    result=redact({"deployment":BASE,"viewport":SIZE,"scene_keys":list(scenes),"provider":provider,"evidence":evidence,"scenes":scenes,"warnings":["Synthetic GPS is visibly labeled.","Viewer basemap may be unavailable; status-only screenshot is preserved.","Metrics are copied from live app state; no scripted numbers."],"remaining_live_runs_after_this":0})
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)); print(json.dumps(result,indent=2))

if __name__=='__main__': main()
