"""Isolated HerSide browser tests; no model, Maps or CDN required."""
from pathlib import Path

import os
import re

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1',
    reason='Run browser tests in a separate process: RUN_BROWSER_TESTS=1 pytest tests/test_chatbot_ui.py')

WEB = Path(__file__).resolve().parents[2] / 'app' / 'web'


@pytest.fixture
def page():
    with sync_playwright() as p:
        channel = os.getenv('PLAYWRIGHT_CHANNEL')
        browser = p.chromium.launch(**({'channel': channel} if channel else {}))
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        page = context.new_page()
        # Real application HTML/CSS, isolated from unrelated map networking.
        html = (WEB / 'index.html').read_text()
        html = re.sub(r'<script[^>]*src="(?!/static/chatbot.js)[^"]*"[^>]*></script>', '', html)
        html = re.sub(r'<link[^>]+href="https://[^>]+>', '', html)
        def serve(route):
            path = route.request.url.split('http://safeher.test')[-1]
            if path == '/':
                route.fulfill(body=html, content_type='text/html')
            elif path.startswith('/static/'):
                file = WEB / path.removeprefix('/static/')
                route.fulfill(body=file.read_text(), content_type='text/javascript' if file.suffix == '.js' else 'text/css')
            else:
                route.abort()
        page.route('http://safeher.test/**', serve)
        page.goto('http://safeher.test/')
        page.locator('#chatbotBtn').click()
        yield page
        browser.close()


def test_dialog_keyboard_and_mobile_layout(page):
    assert page.locator('#chatbotDialog').is_visible()
    assert page.locator('#chatbotInput').evaluate('(el) => el === document.activeElement')
    assert page.locator('#chatbotDialog').evaluate('(el) => el.scrollWidth <= el.clientWidth')
    page.keyboard.press('Escape')
    assert not page.locator('#chatbotDialog').is_visible()
    assert page.locator('#chatbotBtn').evaluate('(el) => el === document.activeElement')


def test_reply_is_text_not_html_and_sources_are_visible(page):
    payloads = []
    def chat(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(json={'reply': '<img src=x onerror=alert(1)>', 'session_id': 's'*32,
            'places': [{'id': 'abc', 'name': 'Test Pharmacy', 'lat': 28.54, 'lon': 77.21,
                        'source': 'google_places', 'distance_m': 200, 'distance_basis': 'straight_line',
                        'hours_basis': 'provider_listing_not_verified', 'open_now': True}],
            'warnings': ['Availability is not verified.'], 'routes': [], 'actions': []})
    page.route('**/api/chat', chat)
    page.locator('[data-chat-prompt]').click()
    page.get_by_text('Test Pharmacy', exact=True).wait_for()
    assert not page.locator('#chatbotMessages img').count()
    assert 'google_places' in page.locator('#chatbotMessages').inner_text()
    assert 'location' not in payloads[0]
    assert page.get_by_role('link', name='Open Google Maps').get_attribute('rel') == 'noopener noreferrer'
    page.get_by_role('button', name='Use these coordinates').click()
    assert '28.54, 77.21' in page.locator('#chatbotInput').input_value()
    page.locator('#chatbotReset').click()
    assert 'Test Pharmacy' not in page.locator('#chatbotMessages').inner_text()


def test_expired_session_and_trip_missing_state(page):
    page.route('**/api/chat', lambda route: route.fulfill(status=404, json={'detail': 'expired'}))
    page.locator('[data-chat-prompt]').click()
    page.get_by_text('This chat session expired.', exact=False).wait_for()
    assert page.locator('#chatbotSend').is_enabled()
    page.locator('#chatbotTrip').click()
    assert 'Select an origin' in page.locator('#chatbotStatus').inner_text()


def test_sos_is_explicit_and_call_is_user_action(page):
    payloads = []
    def chat(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(json={'reply': 'Call 112 in India.', 'urgency': 'urgent',
            'actions': [{'type': 'call', 'label': 'India emergency: 112', 'value': 'tel:112'}]})
    page.route('**/api/chat', chat)
    page.locator('#chatbotSOS').click()
    page.get_by_text('Call 112 in India.', exact=True).wait_for()
    assert payloads[0]['sos'] is True
    assert page.get_by_role('link', name='India emergency: 112').get_attribute('href') == 'tel:112'
    assert page.locator('.herside-emergency').is_visible()


def test_location_requires_click_and_is_sent_once(page):
    page.evaluate('''() => {
      window.gpsCalls = 0;
      Object.defineProperty(window, 'isSecureContext', {value: true});
      Object.defineProperty(navigator, 'geolocation', {value: {
        getCurrentPosition(success) {
          window.gpsCalls++;
          success({coords: {latitude: 28.54, longitude: 77.21, accuracy: 12}, timestamp: Date.now()});
        }
      }});
    }''')
    payloads = []
    def chat(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(json={'reply': 'Result', 'session_id': 's'*32})
    page.route('**/api/chat', chat)
    assert page.evaluate('window.gpsCalls') == 0
    page.locator('#chatbotLocation').click()
    assert 'Not sent yet' in page.locator('#chatbotLocationStatus').inner_text()
    assert not payloads
    page.locator('[data-chat-prompt]').click()
    page.get_by_text('Result', exact=True).wait_for()
    assert payloads[0]['location']['lat'] == 28.54
    assert 'not attached' in page.locator('#chatbotLocationStatus').inner_text()
    page.locator('[data-chat-prompt]').click()
    page.wait_for_function('document.querySelectorAll(".herside-message").length >= 5')
    assert 'location' not in payloads[1]


def test_native_modal_and_focus_trap(page):
    assert page.locator('#chatbotDialog').evaluate('(el) => el.matches(":modal")')
    page.locator('#chatbotReset').focus()
    page.keyboard.press('Tab')
    assert page.locator('#chatbotClose').evaluate('(el) => el === document.activeElement')


def test_selected_trip_is_structured_and_reviewed_before_sending(page):
    page.evaluate('''() => {
      window.state = {origin: {lat:28.5433, lon:77.2067, name:'Hauz Khas Metro'},
                      dest: {lat:28.5286, lon:77.2195, name:'Select Citywalk'}};
      window.departIso = () => '2026-10-16T21:30:00';
    }''')
    payloads = []
    def chat(route):
        payloads.append(route.request.post_data_json)
        route.fulfill(json={'reply': 'Measured comparison', 'routes': []})
    page.route('**/api/chat', chat)
    page.locator('#chatbotTrip').click()
    assert not payloads
    assert '28.5433' not in page.locator('#chatbotInput').input_value()
    assert 'Hauz Khas Metro' in page.locator('#chatbotStatus').inner_text()
    page.locator('#chatbotSend').click()
    page.get_by_text('Measured comparison', exact=True).wait_for()
    assert payloads[0]['trip']['origin']['lat'] == 28.5433
    assert payloads[0]['trip']['depart_at'].endswith('+05:30')
    page.locator('#chatbotReset').click()
    page.locator('#chatbotInput').fill('New question')
    page.locator('#chatbotSend').click()
    page.get_by_text('Measured comparison', exact=True).wait_for()
    assert 'trip' not in payloads[1]


def test_google_maps_sources_follow_the_answer_with_attribution(page):
    """Grounding with Google Maps terms: sources right after the answer, "Google Maps" untranslated,
    the returned title linked to the returned URI."""
    page.route('**/api/chat', lambda route: route.fulfill(json={'reply': 'Cafe Tesu is open until midnight.',
        'sources': [{'title': 'Cafe Tesu - Google Maps', 'uri': 'https://maps.google.com/maps?cid=1',
                     'reviews': [{'title': 'Review', 'uri': 'https://maps.google.com/r1'}]},
                    {'title': 'Bad', 'uri': 'javascript:alert(1)'}]}))
    page.locator('#chatbotInput').fill('Cafes open late near Hauz Khas?')
    page.locator('#chatbotSend').click()
    link = page.get_by_role('link', name='Cafe Tesu - Google Maps')
    link.wait_for()
    assert link.get_attribute('href') == 'https://maps.google.com/maps?cid=1'
    assert link.get_attribute('rel') == 'noopener noreferrer'
    label = page.locator('.herside-gmaps').first
    assert label.inner_text() == 'Google Maps' and label.get_attribute('translate') == 'no'
    assert page.locator('.herside-message').last.locator('p + .herside-sources').count() == 1  # right after the answer
    assert not page.get_by_role('link', name='Bad').count()  # only https sources become links
