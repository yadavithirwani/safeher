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
