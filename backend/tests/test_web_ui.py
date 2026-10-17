"""The web app in a real (headless) browser, against a live server on a free port."""

import socket
import threading
import time

import pytest

from safeher import store

pytestmark = pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")
playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def base_url():
    import uvicorn

    from safeher import api

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(scope="module")
def page(base_url):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(base_url)
        pg.wait_for_selector("[aria-label*='score']", timeout=30_000)  # the default trip is planned
        yield pg
        browser.close()


def route_labels(pg):
    return pg.eval_on_selector_all("[aria-label*='score']", "els => els.map(e => e.getAttribute('aria-label'))")


@pytest.mark.parametrize("box,param,place", [("#destInput", "d", "Lodhi Garden"), ("#originInput", "o", "India Gate")])
def test_enter_right_after_typing_picks_what_was_typed(page, box, param, place):
    """Bug: Enter picked the first item of the stale list (the landmarks shown on focus), so the trip
    didn't change, or became start == destination."""
    page.click(box)
    page.keyboard.type(place)
    page.keyboard.press("Enter")  # no wait: suggestions for this text haven't arrived yet
    word = place.split()[0]
    page.wait_for_function("([k, w]) => (new URLSearchParams(location.hash.slice(1)).get(k) || '').includes(w)",
                           arg=[param, word], timeout=15_000)
    assert page.input_value(box) == place
    assert page.locator("li[data-i]").count() == 0  # the list closed and a late reply didn't reopen it
    page.wait_for_selector("[aria-label*='score']")
    assert len(route_labels(page)) >= 2


def test_changing_the_destination_replans(page):
    page.click("#destInput")
    page.keyboard.type("Lajpat Nagar")
    hit = page.locator("li[data-i]", has_text="Lajpat").first  # on focus the list shows landmarks; wait for results
    hit.wait_for()
    before = route_labels(page)
    hit.click()
    page.wait_for_function("decodeURIComponent(location.hash).includes('Lajpat')", timeout=15_000)
    page.wait_for_function("labels => JSON.stringify([...document.querySelectorAll(\"[aria-label*='score']\")]"
                           ".map(e => e.getAttribute('aria-label'))) !== JSON.stringify(labels)", arg=before, timeout=15_000)
    assert len(route_labels(page)) >= 2


def test_how_is_this_calculated_shows_this_routes_numbers(page):
    """The dialog is built from the selected route's evidence, not a static page."""
    page.goto(page.url.split("#")[0])
    page.wait_for_function("typeof state !== 'undefined' && state.plan")
    ev = page.evaluate("state.plan.routes[state.selected].evidence")
    page.click("#explainBtn")
    body = page.locator("#explainBody")
    body.locator(".ev-formula").wait_for()
    text = body.inner_text()
    assert f"average of {ev['stretches']} stretches" in text
    assert str(round(ev["score"])) in body.locator(".ev-total").inner_text()
    assert body.locator(".ev-signal").count() == 5
    assert "How the method works in general" in text



def test_cold_start_shows_a_full_screen_until_the_first_routes(page, base_url):
    """While the server wakes, a full-screen explanation with real progress covers the app; it goes away
    once the first routes are drawn."""
    if True:  # a fresh page (new context) in the module's browser
        pg = page.context.browser.new_page(viewport={"width": 1280, "height": 800})
        # Record the start-up screen every 100 ms from inside the page.
        pg.add_init_script("""window.__boot = []; const tick = setInterval(() => {
          const el = document.getElementById('bootScreen');
          if (!el) { window.__boot.push('gone'); clearInterval(tick); return; }
          const r = el.getBoundingClientRect();
          const active = [...el.querySelectorAll('[data-step].active')].map(li => li.dataset.step).join(',');
          window.__boot.push([getComputedStyle(el).opacity, Math.round(r.width), Math.round(r.height), active].join('|'));
        }, 100);""")
        def slow(route):  # a sleeping server: the first answers take a few seconds
            import time as _t
            _t.sleep(2.5)
            route.continue_()
        pg.route("**/api/cities", slow)
        pg.goto(base_url)
        pg.wait_for_function("typeof state !== 'undefined' && state.plan", timeout=60000)
        pg.wait_for_function("window.__boot.at(-1) === 'gone'", timeout=5000)
        log = pg.evaluate("window.__boot")
        shown = [x.split("|") for x in log if x != "gone"]
        assert shown and all(op == "1" for op, *_ in shown[:-4])  # opaque from the first paint (fades out at the end)
        assert all(int(w) >= 1280 and int(h) >= 800 for _, w, h, _ in shown)  # covers the whole page
        steps = [a for *_, a in shown]
        assert steps.count("server") >= 10  # "Waking up the server" while the slow answer is pending
        assert steps.index("server") < steps.index("routes")  # then on to scoring the first walk
        assert pg.locator("[aria-label*='score']").count() >= 2
        pg.close()
