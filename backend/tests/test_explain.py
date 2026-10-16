from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from safeher import explain as ex
from safeher.areas import AREAS
from safeher.plan import plan

IST = ZoneInfo("Asia/Kolkata")
TRIPS = {
    "del-hauz-khas-saket": ((28.5433, 77.2067), (28.5286, 77.2195)),
    "blr-indiranagar-mg-road": ((12.9784, 77.6408), (12.9756, 77.6067)),
}
needs_data = pytest.mark.skipif(
    not all((AREAS[a].dir / "walk.graphml").exists() for a in TRIPS), reason="OSM snapshots not fetched")


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
    r = ex.check(good_text(facts) + " It has 73 streetlights.", facts, "en")
    assert any("number not in the data: 73" in p for p in r.problems)


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
