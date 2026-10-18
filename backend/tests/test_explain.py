from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from safeher import explain as ex
from safeher import store
from safeher.plan import plan

IST = ZoneInfo("Asia/Kolkata")
TRIPS = {
    "del-hauz-khas-saket": ((28.5433, 77.2067), (28.5286, 77.2195)),
    "blr-indiranagar-mg-road": ((12.9784, 77.6408), (12.9756, 77.6067)),
}
needs_data = pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")


@pytest.fixture(scope="module")
def delhi_plan():
    o, d = TRIPS["del-hauz-khas-saket"]
    return plan(o, d, datetime(2026, 10, 13, 21, 30, tzinfo=IST))


@pytest.fixture(scope="module")
def facts(delhi_plan):
    return ex.facts_from_plan(delhi_plan)


def good_text(f) -> str:
    best = f["routes"][0]
    short = next(r for r in f["routes"] if r["label"] == "Shortest")
    return (f"The {best['label']} route takes {best['minutes'] - short['minutes']} min longer but scores "
            f"{best['score']} vs {short['score']}, with more places open nearby.")


# --- check() ------------------------------------------------------------------------------

pytestmark = needs_data


def test_good_text_passes(facts):
    assert ex.check(good_text(facts), facts, "en").ok


@pytest.mark.parametrize("word", ["safe", "Safest", "unsafe", "dangerous", "guaranteed"])
def test_forbidden_wording_rejected(facts, word):
    r = ex.check(good_text(facts) + f" It is {word}.", facts, "en")
    assert not r.ok and any("forbidden" in p for p in r.problems)


def test_safer_is_allowed(facts):
    assert ex.check(good_text(facts) + " It may feel safer to some.", facts, "en").ok


def test_invented_number_rejected(facts):
    # Pick a number that's certainly not in this plan's data (a fixed one can collide when data changes).
    allowed = ex.allowed_numbers(facts)
    invented = next(n for n in range(9973, 0, -1) if float(n) not in allowed)
    r = ex.check(good_text(facts) + f" It has {invented} streetlights.", facts, "en")
    assert any(f"number not in the data: {invented}" in p for p in r.problems)


def test_signal_difference_between_routes_allowed(facts):
    best = facts["routes"][0]
    short = next(r for r in facts["routes"] if r["label"] == "Shortest")
    name = max(best["signals"], key=lambda k: abs((best["signals"][k]["value"] or 0) - (short["signals"][k]["value"] or 0)))
    gap = abs(best["signals"][name]["value"] - short["signals"][name]["value"])
    assert gap and float(gap) in ex.allowed_numbers(facts)
    assert ex.check(good_text(facts) + f" Its {name} score is {gap} points higher.", facts, "en").ok


def test_difference_between_the_same_reason_on_two_routes_allowed():
    """'within 150 m of a stop for 39% more of the way' (78% vs 39%) or '23 more places open'."""
    def route(label, score, near, open_):
        return {"label": label, "minutes": 14, "score": score, "signals": {}, "weak_stretches": [],
                "reasons": [f"Passes {open_} places likely open now (6 restaurants)",
                            f"Within 150 m of a bus or metro stop for {near}% of the way"]}
    facts = {"departure_local_time": "22:00", "routes": [route("Most support", 89, 78, 33), route("Shortest", 66, 39, 10)]}
    allowed = ex.allowed_numbers(facts)
    assert {39.0, 23.0} <= allowed and 6.0 in allowed  # 78-39, 33-10; the breakdown's own numbers stay allowed
    assert 27.0 not in allowed  # 33 - 6 mixes two different sentences


def test_must_state_both_scores(facts):
    r = ex.check("The recommended route has more places open nearby and better lighting data overall.", facts, "en")
    assert sum("doesn't state" in p for p in r.problems) == 2


def test_place_not_in_facts_rejected(facts):
    r = ex.check(good_text(facts) + " It passes Qutub Minar.", facts, "en", forbidden_names={"Qutub Minar"})
    assert any("Qutub Minar" in p for p in r.problems)


def test_place_in_facts_allowed(facts):
    stop = next(s["name"] for r in facts["routes"] for s in r["stops"] if s["name"])
    assert ex.check(good_text(facts) + f" It passes {stop}.", facts, "en", forbidden_names={stop}).ok


def test_hindi_must_be_devanagari_and_unmixed(facts):
    best, short = facts["routes"][0]["score"], next(r for r in facts["routes"] if r["label"] == "Shortest")["score"]
    assert "not written in Hindi" in ex.check(good_text(facts), facts, "hi").problems
    mixed = f"मुख्य रoute का स्कोर {best} है और दूसरे का {short} है।"
    assert "mixes scripts inside a word" in ex.check(mixed, facts, "hi").problems
    cyrillic = f"स्कोर {best} और {short} है, 1 миनट ज़्यादा।"
    assert "contains characters from an unexpected script" in ex.check(cyrillic, facts, "hi").problems


def test_hindi_bare_safe_rejected_but_safer_allowed(facts):
    best, short = facts["routes"][0]["score"], next(r for r in facts["routes"] if r["label"] == "Shortest")["score"]
    base = f"इस रास्ते का स्कोर {best} है, दूसरे का {short} है।"
    assert not ex.check(base + " यह सुरक्षित है।", facts, "hi").ok
    assert ex.check(base + " यह अधिक सुरक्षित महसूस हो सकता है।", facts, "hi").ok


# --- template -----------------------------------------------------------------------------


@pytest.mark.parametrize("area", list(TRIPS))
@pytest.mark.parametrize("hour, minute", [(13, 0), (18, 0), (21, 30), (23, 45), (2, 0)])
@pytest.mark.parametrize("lang", ["en", "hi"])
def test_template_always_passes_its_own_checks(area, hour, minute, lang):
    o, d = TRIPS[area]
    f = ex.facts_from_plan(plan(o, d, datetime(2026, 10, 13, hour, minute, tzinfo=IST)))
    text = ex.template_explanation(f, lang)
    assert ex.check(text, f, lang).ok, text


# --- pipeline -----------------------------------------------------------------------------


def test_bad_answer_retried_with_feedback_then_accepted(delhi_plan, facts):
    prompts = []

    def flaky(system, user):
        prompts.append(user)
        return ("It is totally safe." if len(prompts) == 1 else good_text(facts)), "fake-1"

    r = ex.explain(delhi_plan, "en", providers={"fake": flaky}, order=["fake"])
    assert r["provider"] == "fake" and r["text"] == good_text(facts)
    assert "rejected for" in prompts[1] and "forbidden wording" in prompts[1]


def test_persistently_bad_answers_are_an_error_not_a_template(delhi_plan):
    """No silent fallback: answers that fail the fact checks twice surface as an error."""
    with pytest.raises(ex.ExplainFailed, match="failed the fact checks twice"):
        ex.explain(delhi_plan, "en", providers={"fake": lambda s, u: ("Totally safe, 999 lamps.", "fake")},
                   order=["fake"])


def test_failing_configured_provider_is_an_error(delhi_plan):
    def down(system, user):
        raise ex.ProviderUnavailable("HTTP 503: model overloaded")

    with pytest.raises(ex.ExplainFailed, match="Gemini failed: HTTP 503"):
        ex.explain(delhi_plan, "hi", providers={"gemini": down}, order=["gemini"])


def test_unconfigured_provider_means_route_data_summary(delhi_plan):
    def no_key(system, user):
        raise ex.NotConfigured("GEMINI_API_KEY not set")

    r = ex.explain(delhi_plan, "hi", providers={"gemini": no_key}, order=["gemini"])
    assert r["provider"] == "template" and r["lang"] == "hi"


def test_gemini_without_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(ex, "_env", lambda name, default="": "" if name == "GEMINI_API_KEY" else default)
    with pytest.raises(ex.ProviderUnavailable):
        ex.gemini_generate("system", "user")


def test_auto_mode_does_not_use_local_model(monkeypatch):
    monkeypatch.setattr(ex, "_env", lambda name, default="": default)
    assert ex.provider_order() == ["gemini"]


def test_unsupported_language_rejected(delhi_plan):
    with pytest.raises(ValueError):
        ex.explain(delhi_plan, "fr")


def test_prompt_contains_only_facts_json(facts):
    system, user = ex.build_prompt(facts, "hi")
    assert "Hindi" in system and "safe" in system  # the rule itself names the forbidden words
    assert user.startswith("Facts (JSON):") and '"routes"' in user


def test_known_place_containing_a_forbidden_name_is_allowed(facts):
    """Found with real Gemini: naming 'Indiranagar Traffic Police Station' (in the facts) was rejected because
    a different place is called 'Indiranagar'."""
    stop = next(s["name"] for r in facts["routes"] for s in r["stops"] if s["name"])
    first_word = stop.split()[0]
    text = good_text(facts) + f" It passes {stop}."
    assert ex.check(text, facts, "en", forbidden_names={first_word}).ok
    # …but the forbidden place on its own is still caught, and only as a whole word.
    assert not ex.check(good_text(facts) + f" It goes via {first_word}.", facts, "en", forbidden_names={first_word}).ok
    assert ex.check(good_text(facts) + " It is nice.", facts, "en", forbidden_names={"nic"}).ok


# --- Gemini on Vertex AI (Application Default Credentials) ---------------------------------------


def test_vertex_is_used_when_a_cloud_project_is_set(monkeypatch):
    from safeher import explain as ex
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "safeher-demo")
    monkeypatch.setenv("GEMINI_API_KEY", "also-set-but-vertex-wins")
    from google.oauth2.credentials import Credentials as UserCredentials
    monkeypatch.setattr(ex, "_vertex_token", lambda: "tok")
    monkeypatch.setattr(ex, "_adc", UserCredentials(token="tok"))  # gcloud login on a laptop
    url, headers = ex.gemini_target("gemini-x")
    assert url == ("https://aiplatform.googleapis.com/v1/projects/safeher-demo/locations/global/"
                   "publishers/google/models/gemini-x:generateContent")
    assert headers == {"Authorization": "Bearer tok", "x-goog-user-project": "safeher-demo"}
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
    assert ex.gemini_target("gemini-x")[0].startswith("https://asia-south1-aiplatform.googleapis.com/")


def test_api_key_is_used_without_a_cloud_project_and_nothing_is_off(monkeypatch):
    from safeher import explain as ex
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    url, headers = ex.gemini_target("gemini-x")
    assert "generativelanguage.googleapis.com" in url and headers == {"x-goog-api-key": "k"}
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(ex.NotConfigured):
        ex.gemini_target("gemini-x")


def test_missing_adc_with_a_cloud_project_is_an_error_not_off(monkeypatch):
    """Strict: a configured Vertex project without a working sign-in must fail loudly."""
    import google.auth
    import google.auth.exceptions
    from safeher import explain as ex

    def no_adc(**kw):
        raise google.auth.exceptions.DefaultCredentialsError("no credentials")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "safeher-demo")
    monkeypatch.setattr(ex, "_adc", None)
    monkeypatch.setattr(google.auth, "default", no_adc)
    with pytest.raises(ex.ProviderUnavailable, match="application-default login") as e:
        ex.gemini_target("gemini-x")
    assert not isinstance(e.value, ex.NotConfigured)


def test_herside_uses_gemini_on_vertex_when_a_cloud_project_is_set(monkeypatch):
    import os
    from safeher.chatbot import agents
    monkeypatch.setattr(os, "environ", dict(os.environ))  # model() sets env vars: keep them in this test
    os.environ.pop("GOOGLE_CLOUD_LOCATION", None)
    os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
    os.environ["GOOGLE_CLOUD_PROJECT"] = "safeher-demo"
    assert agents.model() == "gemini-2.5-flash"
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "TRUE" and os.environ["GOOGLE_CLOUD_LOCATION"] == "global"
    os.environ["GOOGLE_CLOUD_PROJECT"] = ""
    assert not isinstance(agents.model(), str)  # no Cloud project: local Ollama, as before


def test_one_word_shop_names_dont_flag_ordinary_words(facts):
    """Found with real Gemini answers: a shop called "Options" (and "Care", "Garage") rejected plain
    English. A one-word place only counts when written as a name mid-sentence."""
    shops = {"Options", "Care", "Garage"}
    for extra in (" Options: both routes are lit in parts.", " Take care on the darker stretch.",
                  " Garage entrances line part of the way."):
        assert ex.check(good_text(facts) + extra, facts, "en", forbidden_names=shops).ok, extra
    r = ex.check(good_text(facts) + " It passes the Garage on the main road.", facts, "en", forbidden_names=shops)
    assert not r.ok and any("Garage" in p for p in r.problems)


def test_explain_checks_only_places_along_the_trip():
    """The corridor's places are checked, not all ~30,000 in the city."""
    from safeher import api
    names = api._corridor_place_names((28.5433, 77.2067), (28.5286, 77.2195))
    assert 200 < len(names) < 5000 and "Options" not in names


def test_only_gemini_2_5_models_are_ever_used(monkeypatch):
    """Project rule: Gemini 2.5 Flash / Flash-Lite only, never a newer model, wherever it is configured."""
    from safeher import config
    from safeher.chatbot import agents
    for name in ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash-001"):
        assert ex.gemini_model(name) == name
    for name in ("gemini-3.8-flash", "gemini-3-pro", "gemini-2.5-pro", "gemini-4-argon"):
        with pytest.raises(RuntimeError, match="2.5"):
            ex.gemini_model(name)
    app = config.app()
    for section in ("gemini", "maps_grounding", "assistant_scope"):
        assert ex.gemini_model(app[section]["model"])
    monkeypatch.setenv("CHATBOT_PROVIDER", "gemini")
    monkeypatch.setenv("CHATBOT_MODEL", "gemini-3.8-flash")
    with pytest.raises(RuntimeError):
        agents.model()



def test_a_service_account_does_not_send_a_billing_project(monkeypatch):
    """On Cloud Run, ADC is the service's own account: it bills its own project, and the
    x-goog-user-project header would need an extra role, so it isn't sent."""
    from google.oauth2 import service_account
    from types import SimpleNamespace
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "safeher-demo")
    monkeypatch.setattr(ex, "_vertex_token", lambda: "tok")
    monkeypatch.setattr(ex, "_adc", SimpleNamespace(__class__=service_account.Credentials))
    assert ex.gemini_target("gemini-x")[1] == {"Authorization": "Bearer tok"}



def test_a_shared_vertex_api_key_keeps_gemini_2_5_and_beats_an_ai_studio_key(monkeypatch):
    """VERTEX_API_KEY: one key a team shares, billed to the Cloud project, still serving Gemini 2.5
    (AI Studio keys can't use 2.5 any more). Used when no project login is configured."""
    monkeypatch.setenv("VERTEX_API_KEY", "vk")
    monkeypatch.setenv("GEMINI_API_KEY", "ai-studio")
    url, headers = ex.gemini_target("gemini-2.5-flash")
    assert url == "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash:generateContent"
    assert headers == {"x-goog-api-key": "vk"}  # in a header, not the URL (URLs end up in logs)


def test_a_project_login_wins_over_the_shared_key(monkeypatch):
    """ADC answered ~12 s faster per call than the key in testing, so with both set ADC is used."""
    monkeypatch.setenv("VERTEX_API_KEY", "vk")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "safeher-demo")
    monkeypatch.setattr(ex, "_vertex_token", lambda: "tok")
    url, headers = ex.gemini_target("gemini-2.5-flash")
    assert "/projects/safeher-demo/" in url and "x-goog-api-key" not in headers
    assert ex.vertex_api_key() == ""


def test_herside_passes_the_shared_vertex_key_explicitly(monkeypatch):
    """google-genai reads a key from the environment only for the Gemini API, so HerSide passes the
    shared key in an explicit Vertex AI client."""
    from safeher.chatbot import agents
    monkeypatch.setenv("VERTEX_API_KEY", "vk")
    m = agents.model()
    client = m.api_client._api_client
    assert m.model == "gemini-2.5-flash" and client.vertexai and client.api_key == "vk" and client.project is None
    assert agents.describe() == {"provider": "gemini", "model": "gemini-2.5-flash", "platform": "Vertex AI"}
