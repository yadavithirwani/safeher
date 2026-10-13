import json

import pytest

from safeher import ncrb
from safeher.areas import AREAS

pytestmark = pytest.mark.skipif(not ncrb.OUT_JSON.exists(), reason="run: python -m safeher.ncrb")


@pytest.fixture(scope="module")
def table():
    return json.loads(ncrb.OUT_JSON.read_text())


def test_only_territorial_districts(table):
    assert len(table["districts"]) == 15
    assert not set(table["districts"]) & ncrb.NON_TERRITORIAL


def test_percentile_follows_case_counts(table):
    ds = sorted(table["districts"].values(), key=lambda d: d["avg_cases_per_year"])
    assert [d["city_percentile"] for d in ds] == sorted(d["city_percentile"] for d in ds)
    assert ds[0]["city_percentile"] == 0 and ds[-1]["city_percentile"] == 1


def test_excludes_domestic_heads():
    assert not any("husband" in h or "dowry" in h or "domestic" in h for h in ncrb.PUBLIC_SPACE_HEADS)
    assert "2020" not in ncrb.YEARS


def test_delhi_area_gets_low_confidence_value():
    percentile, confidence, note = ncrb.lookup(AREAS["del-hauz-khas-saket"])
    assert 0 <= percentile <= 1
    assert confidence <= 0.2
    assert "South" in note


def test_bengaluru_has_no_sub_city_data():
    assert ncrb.lookup(AREAS["blr-indiranagar-mg-road"]) is None
