"""Build SafeHer's reference database: every supported city into one DuckDB file.

    python -m safeher.data build                    all cities in config/cities.json
    python -m safeher.data build --city delhi       one city (others are copied from the current database)
    python -m safeher.data info                     what the current database holds

Sources (each fetched once per build, nothing at request time):
  OpenStreetMap    Geofabrik regional extract (.osm.pbf, checksum-verified): walkable street segments
                   (OSMnx "walk" rules), places used for scoring and quick actions, district boundaries
  Night lights     NASA Black Marble VNP46A4 via LAADS (needs EARTHDATA_TOKEN in backend/.env)
  Crime            NCRB district-wise crimes against women via the India Data Portal (CKAN, no key);
                   city-wide police figures where a city publishes them (Bengaluru City Police via OpenCity)

The new database is written next to the old one and swapped in only after every check passes, so a
failed build never leaves the app without data. All failures are errors; nothing is skipped silently.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import duckdb
import httpx

from . import config
from .config import REPO_ROOT
from .geo import haversine_m

C = config.load("cities")
OUTPUT = REPO_ROOT / C["output"]
RAW = REPO_ROOT / C["raw_dir"]
USER_AGENT = "SafeHer/0.3 (hackathon prototype; github.com/yadavithirwani/safeher)"

# --- What to keep from OpenStreetMap ----------------------------------------------------------

_EXCLUDED_HIGHWAY = re.compile(r"abandoned|bus_guideway|construction|cycleway|motor|no|planned|platform|"
                               r"proposed|raceway|razed")  # OSMnx 2.x "walk" network filter
EDGE_TAGS = ("highway", "lit", "name", "sidewalk", "footway")
_AMENITY = {"police", "hospital", "clinic", "doctors", "pharmacy", "toilets", "fuel", "cafe", "restaurant",
            "fast_food", "bank", "atm"}
_SHOP = {"convenience", "supermarket", "chemist", "mall", "department_store"}


def walkable(tags) -> bool:
    hw = tags.get("highway")
    if not hw or _EXCLUDED_HIGHWAY.search(hw):
        return False
    if tags.get("area") == "yes" or tags.get("foot") == "no":
        return False
    return not (tags.get("service") == "private" or tags.get("access") == "private")


_SEARCH_KEYS = ("amenity", "shop", "tourism", "leisure", "historic", "office", "railway", "public_transport",
                "healthcare")
NEIGHBOURHOODS = {"suburb", "neighbourhood", "quarter", "locality", "village", "town", "hamlet"}


def useful_place(tags, is_node: bool) -> bool:
    """Places used for scoring (named or not), plus every named place people may search for."""
    if tags.get("amenity") in _AMENITY or tags.get("shop") in _SHOP:
        return True
    if tags.get("office") == "government" and tags.get("government") == "police":
        return True
    if is_node and tags.get("highway") in ("bus_stop", "street_lamp"):
        return True
    if tags.get("public_transport") == "station" or tags.get("railway") in ("station", "subway_entrance", "halt"):
        return True
    if not tags.get("name"):
        return False
    if is_node and tags.get("place") in NEIGHBOURHOODS:
        return True
    return any(tags.get(k) for k in _SEARCH_KEYS)


# --- Steps ---------------------------------------------------------------------------------------


def _say(msg: str) -> None:
    print(msg, flush=True)


def ensure_extract(city: dict) -> Path:
    """Download the Geofabrik extract if missing or stale vs its .md5, and verify the checksum."""
    path = RAW / "osm" / city["extract"]
    url = C["osm_extract_base_url"] + city["extract"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        expected = c.get(url + ".md5").raise_for_status().text.split()[0]
        if path.exists() and _md5(path) == expected:
            return path
        _say(f"  downloading {city['extract']} …")
        tmp = path.with_suffix(".part")
        with c.stream("GET", url) as r, tmp.open("wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    if _md5(tmp) != expected:
        tmp.unlink()
        raise RuntimeError(f"{city['extract']}: checksum mismatch after download")
    tmp.replace(path)
    return path


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_osm(city: dict, extract: Path) -> tuple[list, list, list]:
    """One pass: (segments, places, districts) inside the city's bbox."""
    import osmium
    from shapely import wkt

    b = city["bbox"]
    inside = lambda lat, lon: b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]  # noqa: E731
    want_level = (city.get("districts") or {}).get("admin_level")
    segs, places, districts = [], [], []
    seen = set()
    wktf = osmium.geom.WKTFactory()
    fp = osmium.FileProcessor(str(extract)).with_locations()
    if want_level:
        fp = fp.with_areas(osmium.filter.TagFilter(("boundary", "administrative")))
    for obj in fp:
        if obj.is_node():
            if obj.tags and obj.location.valid() and inside(obj.location.lat, obj.location.lon) \
                    and useful_place(obj.tags, True):
                places.append((city["id"], f"node/{obj.id}", obj.location.lat, obj.location.lon,
                               json.dumps(dict(obj.tags), ensure_ascii=False)))
        elif obj.is_way():
            tags = obj.tags
            locs = [(n.ref, n.location.lat, n.location.lon) for n in obj.nodes if n.location.valid()]
            if len(locs) < 2:
                continue
            if useful_place(tags, False):
                lat = sum(p[1] for p in locs) / len(locs)
                lon = sum(p[2] for p in locs) / len(locs)
                if inside(lat, lon):
                    places.append((city["id"], f"way/{obj.id}", lat, lon, json.dumps(dict(tags), ensure_ascii=False)))
            if walkable(tags) and any(inside(p[1], p[2]) for p in locs):
                t = tuple(tags.get(k) for k in EDGE_TAGS)
                for (u, la1, lo1), (v, la2, lo2) in zip(locs, locs[1:]):
                    segs.append((city["id"], obj.id, u, v, la1, lo1, la2, lo2, haversine_m((la1, lo1), (la2, lo2)), *t))
        elif obj.is_area() and want_level:
            tags = obj.tags
            if tags.get("boundary") != "administrative" or tags.get("admin_level") != want_level:
                continue
            name = tags.get("name:en") or tags.get("name")
            key = (name, obj.orig_id())
            if not name or key in seen:
                continue
            try:
                geom = wkt.loads(wktf.create_multipolygon(obj))
            except Exception:  # broken boundary relations exist; skip them, they're reported below
                continue
            c = geom.representative_point()
            if inside(c.y, c.x):
                seen.add(key)
                districts.append((city["id"], name, geom.wkt))
    return segs, places, districts


def night_lights(city: dict) -> list:
    from .viirs import fetch_bbox

    b = city["bbox"]
    grid = fetch_bbox(b["south"], b["west"], b["north"], b["east"], int(C["night_lights"]["year"]))
    rows = []
    for i, lat in enumerate(grid.lats):
        for j, lon in enumerate(grid.lons):
            v = float(grid.radiance[i, j])
            if v == v:  # skip NaN (no valid observation)
                rows.append((city["id"], float(lat), float(lon), v))
    return rows


def incidents(city: dict) -> list:
    """NCRB averages per police district, with each district's rank within the city (0 = fewest)."""
    d = city.get("districts")
    if not d:
        return []
    n = C["ncrb"]
    rows = [r for r in csv.DictReader(io.StringIO(_download(n["url"], "ncrb")))
            if r["state_name"] == d["ncrb_state"] and r["year"] in n["years"]
            and r["registration_circles"] not in d["ncrb_exclude"]]
    counts: dict[str, list[float]] = {}
    for r in rows:
        counts.setdefault(r["registration_circles"], []).append(sum(float(r[h] or 0) for h in n["heads"]))
    short = {k: len(v) for k, v in counts.items() if len(v) != len(n["years"])}
    if short:
        raise RuntimeError(f"NCRB: districts missing years: {short}")
    avg = {k: sum(v) / len(v) for k, v in counts.items()}
    ordered = sorted(avg.values())
    return [(city["id"], k, round(a, 1), round(sum(x < a for x in ordered) / (len(ordered) - 1), 3),
             float(n["confidence"]), ",".join(n["years"])) for k, a in sorted(avg.items())]


def _download(url: str, folder: str) -> str:
    """Fetch a small source file once into data/raw/<folder>/ and return its text."""
    path = RAW / folder / Path(url).name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        path.write_bytes(r.content)
    return path.read_text(encoding="utf-8-sig")


def city_stats(city: dict) -> list:
    """City-wide police figures (context only): (city, source, year, head, cases) per configured head."""
    s = city.get("city_stats")
    if not s:
        return []
    rows = {r["Type of Crime"].strip(): r for r in csv.DictReader(io.StringIO(_download(s["url"], "police")))}
    missing = [h for h in s["heads"] if h not in rows] + [
        f"{y} Reported" for y in s["years"] if f"{y} Reported" not in next(iter(rows.values()), {})]
    if missing:
        raise RuntimeError(f"{city['name']} police figures: not in the file (has its format changed?): {missing}")
    return [(city["id"], s["source"], int(y), label, int(rows[h][f"{y} Reported"]))
            for h, label in s["heads"].items() for y in s["years"]]


# --- Build ----------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE segments (city VARCHAR, way BIGINT, u BIGINT, v BIGINT, lat1 DOUBLE, lon1 DOUBLE, lat2 DOUBLE,
                       lon2 DOUBLE, length DOUBLE, highway VARCHAR, lit VARCHAR, name VARCHAR, sidewalk VARCHAR,
                       footway VARCHAR);
CREATE TABLE places (city VARCHAR, osm VARCHAR, lat DOUBLE, lon DOUBLE, tags VARCHAR);
CREATE TABLE districts (city VARCHAR, name VARCHAR, wkt VARCHAR);
CREATE TABLE night_lights (city VARCHAR, lat DOUBLE, lon DOUBLE, radiance DOUBLE);
CREATE TABLE incidents (city VARCHAR, district VARCHAR, avg_cases DOUBLE, percentile DOUBLE, confidence DOUBLE,
                        years VARCHAR);
CREATE TABLE city_stats (city VARCHAR, source VARCHAR, year INTEGER, head VARCHAR, cases INTEGER);
CREATE TABLE sources (city VARCHAR, source VARCHAR, detail VARCHAR, built_at VARCHAR);
"""


def check(con, city: dict) -> list[str]:
    """Problems that should stop a build from being swapped in."""
    cid = city["id"]
    q = lambda sql: con.execute(sql, [cid]).fetchone()[0]  # noqa: E731
    problems = []
    if q("SELECT count(*) FROM segments WHERE city = ?") < 10_000:
        problems.append("fewer than 10,000 street segments")
    for kind in ("police", "toilets", "hospital", "pharmacy"):
        n = q(f"SELECT count(*) FROM places WHERE city = ? AND (json_extract_string(tags, '$.amenity') = '{kind}')")
        if n == 0:
            problems.append(f"no {kind} places")
    if q("SELECT count(*) FROM night_lights WHERE city = ?") < 100:
        problems.append("night-light grid missing or tiny")
    if city.get("districts"):
        mapped = set(city["districts"]["osm_to_ncrb"].values())
        have = {r[0] for r in con.execute("SELECT district FROM incidents WHERE city = ?", [cid]).fetchall()}
        if not mapped <= have:
            problems.append(f"NCRB districts missing: {sorted(mapped - have)}")
        osm_names = {r[0] for r in con.execute("SELECT name FROM districts WHERE city = ?", [cid]).fetchall()}
        if not set(city["districts"]["osm_to_ncrb"]) <= osm_names:
            problems.append(f"OSM districts missing: {sorted(set(city['districts']['osm_to_ncrb']) - osm_names)}")
    if city.get("city_stats"):
        want = len(city["city_stats"]["heads"]) * len(city["city_stats"]["years"])
        if q("SELECT count(*) FROM city_stats WHERE city = ? AND cases >= 0") != want:
            problems.append("city-wide police figures incomplete")
    return problems


def build(only: list[str] | None = None) -> None:
    import pandas as pd

    cities = [c for c in C["cities"] if not only or c["id"] in only]
    unknown = set(only or []) - {c["id"] for c in C["cities"]}
    if unknown:
        raise SystemExit(f"unknown city: {', '.join(sorted(unknown))}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute(SCHEMA)
    keep = [c["id"] for c in C["cities"] if c not in cities]
    if keep and OUTPUT.exists():  # rebuilding some cities: carry the others over unchanged
        con.execute(f"ATTACH '{OUTPUT}' AS old (READ_ONLY)")
        old_tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables "
                                               "WHERE table_catalog = 'old'").fetchall()}
        for t in ("segments", "places", "districts", "night_lights", "incidents", "city_stats", "sources"):
            if t in old_tables:  # a table added since the old build has nothing to carry over
                con.execute(f"INSERT INTO {t} SELECT * FROM old.{t} WHERE city IN ({','.join('?' * len(keep))})", keep)
        con.execute("DETACH old")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for city in cities:
        t0 = time.time()
        _say(f"{city['name']}:")
        extract = ensure_extract(city)
        segs, places, districts = read_osm(city, extract)
        _say(f"  OpenStreetMap: {len(segs):,} street segments, {len(places):,} places, {len(districts)} districts "
             f"({time.time() - t0:.0f}s)")
        lights = night_lights(city)
        _say(f"  NASA night lights: {len(lights):,} pixels")
        inc = incidents(city)
        _say(f"  NCRB: {len(inc)} police districts" if inc else "  NCRB: no sub-city data for this city")
        stats = city_stats(city)
        if stats:
            _say(f"  City police: {len(stats)} city-wide figures ({stats[0][1]})")
        for name, rows, cols in (
                ("segments", segs, ["city", "way", "u", "v", "lat1", "lon1", "lat2", "lon2", "length", *EDGE_TAGS]),
                ("places", places, ["city", "osm", "lat", "lon", "tags"]),
                ("districts", districts, ["city", "name", "wkt"]),
                ("night_lights", lights, ["city", "lat", "lon", "radiance"]),
                ("incidents", inc, ["city", "district", "avg_cases", "percentile", "confidence", "years"]),
                ("city_stats", stats, ["city", "source", "year", "head", "cases"])):
            if rows:
                df = pd.DataFrame(rows, columns=cols)
                con.register("df", df)
                con.execute(f"INSERT INTO {name} SELECT * FROM df")
                con.unregister("df")
        for source, detail in (("openstreetmap", f"{city['extract']} (md5 verified)"),
                               ("night_lights", f"{C['night_lights']['product']} {C['night_lights']['year']}"),
                               ("ncrb", ",".join(C["ncrb"]["years"]) if inc else "none"),
                               ("city_police", stats[0][1] if stats else "none")):
            con.execute("INSERT INTO sources VALUES (?, ?, ?, ?)", [city["id"], source, detail, stamp])
        problems = check(con, city)
        if problems:
            con.close()
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"{city['name']}: build failed checks: " + "; ".join(problems))
    # Sorted by location so a corridor query reads only a few blocks of the file.
    for t, order in (("segments", "city, lat1, lon1"), ("places", "city, lat, lon")):
        con.execute(f"CREATE TABLE {t}_sorted AS SELECT * FROM {t} ORDER BY {order}")
        con.execute(f"DROP TABLE {t}")
        con.execute(f"ALTER TABLE {t}_sorted RENAME TO {t}")
    con.close()
    tmp.replace(OUTPUT)
    _say(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1e6:.0f} MB)")


def info() -> None:
    if not OUTPUT.exists():
        print(f"{OUTPUT} not built yet: run python -m safeher.data build")
        return
    con = duckdb.connect(str(OUTPUT), read_only=True)
    for (cid,) in con.execute("SELECT DISTINCT city FROM segments ORDER BY 1").fetchall():
        n = {t: con.execute(f"SELECT count(*) FROM {t} WHERE city = ?", [cid]).fetchone()[0]
             for t in ("segments", "places", "districts", "night_lights", "incidents", "city_stats")}
        built = con.execute("SELECT max(built_at) FROM sources WHERE city = ?", [cid]).fetchone()[0]
        print(f"{cid}: " + ", ".join(f"{v:,} {k}" for k, v in n.items()) + f" · built {built}")
    con.close()
    print(f"{OUTPUT.stat().st_size / 1e6:.0f} MB")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m safeher.data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--city", action="append", help="build only this city (repeatable)")
    sub.add_parser("info")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.city)
    else:
        info()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
