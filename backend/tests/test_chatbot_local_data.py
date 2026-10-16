"""Local data and hybrid tool tests: synthetic graph/POIs, no provider calls."""
import asyncio
from types import SimpleNamespace

import networkx as nx
import pytest

from safeher.areas import AREAS
from safeher.chatbot import local_data, tools
from safeher.chatbot.maps import MapsClient, MapsUnavailable
from safeher.pois import Poi, PointIndex


@pytest.fixture
def data(monkeypatch):
    area = AREAS['del-hauz-khas-saket']
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
    data = SimpleNamespace(area=area, pois=pois, graph=graph, snap=snap,
        safe_spaces=PointIndex((p.latlon, p) for p in pois), stops=PointIndex(()))
    monkeypatch.setattr(local_data, 'AREAS', {area.id: area})
    monkeypatch.setattr(local_data, 'load_area', lambda _: data)
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
