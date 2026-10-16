"""Recording-only map style: the default trip's streets from data/safeher.duckdb."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def style():
    from safeher import config, store
    from safeher.cities import CITIES, corridor

    city = CITIES[config.app()["default_city"]]
    o, d = (next(lm for lm in city.landmarks if lm.name == n) for n in city.default_trip)
    graph = store.corridor_data(corridor((o.lat, o.lon), (d.lat, d.lon))).graph
    features = []
    for u, v, edge in graph.edges(data=True):
        if u > v:
            continue
        geometry = edge.get("geometry")
        coords = list(geometry.coords) if geometry is not None else [
            [graph.nodes[u]["x"], graph.nodes[u]["y"]],
            [graph.nodes[v]["x"], graph.nodes[v]["y"]],
        ]
        features.append({"type": "Feature", "properties": {},
                         "geometry": {"type": "LineString", "coordinates": coords}})
    return {"version": 8, "sources": {"streets": {"type": "geojson",
        "attribution": "© OpenStreetMap contributors · cached demo streets",
        "data": {"type": "FeatureCollection", "features": features}}}, "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": "#f4f0e8"}},
        {"id": "street-outline", "type": "line", "source": "streets",
         "paint": {"line-color": "#c6c1b7", "line-width": 6}},
        {"id": "street", "type": "line", "source": "streets",
         "paint": {"line-color": "#ffffff", "line-width": 3}},
    ]}


def configure(context, cache: Path, map_style):
    context.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    context.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    context.route("https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.js",
                  lambda route: route.fulfill(path=str(cache / "maplibre.js"), content_type="application/javascript"))
    context.route("https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.css",
                  lambda route: route.fulfill(path=str(cache / "maplibre.css"), content_type="text/css"))
    context.route("https://basemaps.cartocdn.com/**",
                  lambda route: route.fulfill(json=map_style))
