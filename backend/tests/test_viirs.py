import numpy as np
import pytest

from safeher import signals as sig
from safeher import store
from safeher.viirs import ViirsGrid


@pytest.fixture
def grid():
    # 3x3 pixels of 0.005° centred on (28.54, 77.21); one dark pixel, one with no observation.
    radiance = np.array([[40.0, 35.0, 30.0],
                         [25.0, 2.0, 20.0],
                         [15.0, np.nan, 10.0]])
    return ViirsGrid(radiance, lats=np.array([28.545, 28.540, 28.535]),
                     lons=np.array([77.205, 77.210, 77.215]), year=2025)


def test_lookup_picks_containing_pixel(grid):
    assert grid.radiance_at(28.5401, 77.2101) == 2.0
    assert grid.radiance_at(28.5449, 77.2049) == 40.0


def test_outside_grid_and_missing_pixels_return_none(grid):
    assert grid.radiance_at(28.60, 77.21) is None
    assert grid.radiance_at(28.535, 77.210) is None  # NaN pixel
    assert grid.percentile_at(28.60, 77.21) is None


def test_percentile_ranks_within_area(grid):
    assert grid.percentile_at(28.540, 77.210) == 0.0  # darkest
    assert grid.percentile_at(28.545, 77.205) == 1.0  # brightest
    assert 0 < grid.percentile_at(28.540, 77.215) < 1


def test_all_nan_grid_rejected():
    with pytest.raises(ValueError):
        ViirsGrid(np.full((2, 2), np.nan), np.array([1.0, 0.99]), np.array([1.0, 1.01]), 2025)


# --- lighting signal precedence -------------------------------------------------------------


def test_osm_tag_beats_viirs():
    r = sig.lighting({"lit": "no"}, 0, night=True, viirs_percentile=0.95)
    assert r.value == 0 and r.confidence == 0.9


def test_mapped_lamps_beat_viirs():
    r = sig.lighting({}, 1, night=True, viirs_percentile=0.0)
    assert r.value == 80 and r.confidence == 0.7


def test_viirs_beats_road_type_guess():
    r = sig.lighting({"highway": "primary"}, 0, night=True, viirs_percentile=0.1)
    assert r.value == 10 and r.confidence == 0.45
    assert "VIIRS" in r.note


def test_viirs_less_certain_than_lamps_more_than_guess():
    viirs = sig.lighting({}, 0, night=True, viirs_percentile=0.5).confidence
    lamps = sig.lighting({}, 1, night=True).confidence
    guess = sig.lighting({"highway": "residential"}, 0, night=True).confidence
    assert guess < viirs < lamps


# --- real data (data/safeher.duckdb) -----------------------------------------------------------


@pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")
def test_deer_park_darker_than_aurobindo_marg():
    g = store.night_lights("delhi")
    deer_park = g.radiance_at(28.5527, 77.1985)
    aurobindo_marg = g.radiance_at(28.5434, 77.2066)  # outside Hauz Khas metro
    assert deer_park is not None and aurobindo_marg is not None
    assert deer_park < aurobindo_marg


@pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")
def test_corridors_rank_brightness_against_the_whole_city():
    """A trip corridor uses its city's grid, so 'bright' means bright for the city, not the street."""
    from safeher.cities import corridor
    data = store.corridor_data(corridor((28.5433, 77.2067), (28.5286, 77.2195)))
    assert data.viirs is store.night_lights("delhi")
    assert data.viirs.radiance.shape[0] > 50  # city-sized, not corridor-sized


@pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")
def test_grid_rebuilt_from_rows_matches_every_stored_pixel():
    rows = store.con().execute("SELECT lat, lon, radiance FROM night_lights WHERE city = 'delhi' "
                               "USING SAMPLE 200 ROWS").fetchall()
    g = store.night_lights("delhi")
    assert all(g.radiance_at(lat, lon) == pytest.approx(v) for lat, lon, v in rows)
