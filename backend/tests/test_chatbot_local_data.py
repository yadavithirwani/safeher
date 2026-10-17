"""Local data and hybrid tool tests: synthetic graph/POIs, no provider calls."""
import asyncio
from types import SimpleNamespace

import networkx as nx
import pytest

from safeher.chatbot import local_data, tools
from safeher.chatbot.maps import MapsClient, MapsUnavailable
from safeher.cities import CITIES, Unsupported, city_at
from safeher.pois import Poi


@pytest.fixture
def data(monkeypatch):
    city = CITIES['delhi']
    pois = [
        Poi('node/1', 'pharmacy', 28.540, 77.210, 'Lotus Pharmacy', tags={'alt_name': 'Lotus Chemist', 'phone': '123'}),
        Poi('way/2', 'pharmacy', 28.54001, 77.21001, 'Lotus Pharmacy'),
        Poi('node/3', 'pharmacy', 28.541, 77.210, 'Private Pharmacy', tags={'access': 'private'}),
        Poi('node/4', 'pharmacy', 28.542, 77.210, 'Other Pharmacy'),
    ]
    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, length=200)
    graph.add_edge(1, 2, length=100)
    graph.add_edge(2, 3, length=50)
    graph.add_node(4)
    nodes = {(28.54, 77.21): (1, 10), (28.55, 77.22): (3, 20),
             (28.545, 77.215): (4, 0)}
    def snap(point):
        if point not in nodes:
            raise ValueError('no nearby node')
        return nodes[point]
    data = SimpleNamespace(area=SimpleNamespace(city=city.id), pois=pois, graph=graph, snap=snap)
    def corridor_data(origin, destination):
        if city_at(*origin) is None or city_at(*destination) is None:
            raise Unsupported('outside')
        return data
    indexed = {c: local_data.index_city(CITIES[c], pois if c == 'delhi' else []) for c in CITIES}
    monkeypatch.setattr(local_data, 'city_data', indexed.__getitem__)
    monkeypatch.setattr(local_data, 'corridor_data', corridor_data)
    monkeypatch.setattr(local_data, 'snapshot', lambda city: '2026-10-16T12:00:00Z')
    monkeypatch.setattr(local_data, 'neighbourhood', lambda city_id, p: None)
    return data


def test_search_normalization_aliases_and_fuzzy(data):
    assert local_data.local_search('LOTUS chemist Delhi')[0]['id'] == 'node/1'
    assert local_data.local_search('Lotus Pharmcy')[0]['match'] == 'fuzzy'
    assert local_data.local_search('hauz-khas metro')[0]['source'] == 'safeher_landmark'
    assert local_data.local_search('zzzzzzzzzz') == []
    assert local_data.local_search('') == []


def test_duplicate_places_access_and_provenance(data):
    places = local_data.local_places('pharmacy', 28.54, 77.21)
    assert [p['name'] for p in places] == ['Lotus Pharmacy', 'Other Pharmacy']
    assert places[0]['hours_basis'] == 'assumed'
    assert places[0]['snapshot_at']
    assert places[0]['attributions']
    assert places[0]['distance_basis'] == 'straight_line'
    assert local_data.local_places('pharmacy', 0, 0) == []


def test_local_details_use_cached_id(data):
    place = local_data.local_places('pharmacy', 28.54, 77.21)[0]
    assert local_data.local_details(place)['details']['phone'] == '123'
    assert 'error' in local_data.local_details({**place, 'id': 'not-present'})


def test_walking_directed_network_and_connectors(data):
    result = local_data.local_walking((28.54, 77.21), (28.55, 77.22))
    route = result['routes'][0]
    assert route['network_distance_m'] == 150
    assert route['connector_distance_m'] == 30
    assert route['distance_m'] == 180
    assert route['duration_min'] == 2.2  # rounded at 80 m/min
    assert result['scored'] is False
    assert not local_data.local_walking((28.55, 77.22), (28.54, 77.21))['routes']
    assert not local_data.local_walking((28.54, 77.21), (28.545, 77.215))['routes']
    assert not local_data.local_walking((0, 0), (28.54, 77.21))['routes']


def test_resolve_uses_local_before_google(data, monkeypatch):
    async def forbidden(*args):
        pytest.fail('Local candidate should avoid Google geocoding')
    monkeypatch.setattr(MapsClient, 'resolve', forbidden)
    result = asyncio.run(tools.resolve_location('Lotus Chemist', SimpleNamespace(state={})))
    assert result['candidates'][0]['requires_confirmation']


def test_resolve_unavailable_is_explicit(data, monkeypatch):
    async def unavailable(*args):
        raise MapsUnavailable('denied')
    monkeypatch.setattr(MapsClient, 'resolve', unavailable)
    context = SimpleNamespace(state={})
    result = asyncio.run(tools.resolve_location('unmapped unknown', context))
    assert result['error'] == 'location_unresolved'
    assert tools.render_evidence(context.state['turn:evidence']).needs_clarification


def test_empty_google_search_uses_local(data, monkeypatch):
    async def empty(*args):
        return {'places': [], 'source': 'google_places'}
    monkeypatch.setattr(MapsClient, 'nearby', empty)
    context = SimpleNamespace(state={'turn:coordinate_evidence': '28.54,77.21'})
    result = asyncio.run(tools.search_nearby_places('pharmacy', 28.54, 77.21, context))
    assert result['source'] == 'openstreetmap'
    assert len(result['places']) == 2
    details = asyncio.run(tools.get_place_details('node/1', context))
    assert details['details']['phone'] == '123'


def test_google_success_is_not_mixed_with_osm(data, monkeypatch):
    async def google(*args):
        return {'places': [{'id': 'google-id', 'source': 'google_places'}], 'source': 'google_places'}
    monkeypatch.setattr(MapsClient, 'nearby', google)
    context = SimpleNamespace(state={'turn:coordinate_evidence': '28.54,77.21'})
    result = asyncio.run(tools.search_nearby_places('pharmacy', 28.54, 77.21, context))
    assert [p['id'] for p in result['places']] == ['google-id']


def test_walking_fallback_and_search_destination(data, monkeypatch):
    async def unavailable(*args):
        raise MapsUnavailable('down')
    monkeypatch.setattr(MapsClient, 'walking', unavailable)
    context = SimpleNamespace(state={'turn:coordinate_evidence': '28.54,77.21',
        'turn:evidence': [{'places': [{'lat': 28.55, 'lon': 77.22}]}]})
    result = asyncio.run(tools.estimate_walking_access(28.54, 77.21, 28.55, 77.22, context))
    assert result['source'] == 'openstreetmap'
    response = tools.render_evidence([result])
    assert 'Google walking estimates' not in response.reply
    assert response.routes[0]['distance_m'] == 180


def test_search_candidate_is_not_silently_confirmed(data):
    context = SimpleNamespace(state={'turn:evidence': [{'candidates': [{'lat': 28.55, 'lon': 77.22}]}]})
    with pytest.raises(ValueError):
        tools.confirmed_point(28.55, 77.22, context)


# --- Real data (data/safeher.duckdb): the chatbot searches both whole cities -----------------------
from safeher import store  # noqa: E402

needs_db = pytest.mark.skipif(not store.available(), reason="run: python -m safeher.data build")


@needs_db
@pytest.mark.parametrize("query,name,city", [
    ("Toit", "Toit", "Bengaluru"), ("Bob's Bar", "Bob's Bar", "Bengaluru"), ("bobs bar bangalore", "Bob's Bar", "Bengaluru"),
    ("India Gate Delhi", "India Gate", "Delhi"), ("Lodhi Garden", "Lodhi Garden", "Delhi"),
    ("hauz khas metro", "Hauz Khas Metro", "Delhi"),
    # How people (and the model) phrase it: filler words and a neighbourhood around the name.
    ("Toit in Bengaluru", "Toit", "Bengaluru"), ("near Toit", "Toit", "Bengaluru"),
    ("Toit Indiranagar", "Toit", "Bengaluru"), ("pharmacy near Toit", "Toit", "Bengaluru"), ("the Lodhi Garden in New Delhi", "Lodhi Garden", "Delhi")])
def test_real_search_finds_places_city_wide(query, name, city):
    """Ported from the two demo areas to the whole of both cities: pubs, monuments and parks are found."""
    top = local_data.local_search(query)[0]
    assert (top["name"], top["city"]) == (name, city) and top["match"] in ("exact", "contained")
    assert top["requires_confirmation"] and top["neighbourhood"] and top["snapshot_at"]


@needs_db
def test_real_same_named_places_are_told_apart_by_neighbourhood():
    hits = local_data.local_search("Bob's Bar")
    assert len(hits) > 1 and len({h["neighbourhood"] for h in hits}) == len(hits)


@needs_db
def test_real_places_and_walking_outside_the_old_demo_areas():
    """Koramangala was outside both old demo areas; city-wide data covers it."""
    assert local_data.local_places("pharmacy", 12.9352, 77.6245)
    walk = local_data.local_walking((12.9352, 77.6245), (12.9279, 77.6271))
    assert walk["routes"] and 500 < walk["routes"][0]["distance_m"] < 2500
    assert local_data.local_walking((19.07, 72.87), (19.08, 72.88))["error"] == "outside_supported_cities"
