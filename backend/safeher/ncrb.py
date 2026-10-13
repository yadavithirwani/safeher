"""Historical incidents from NCRB "Crime in India" (district-wise crimes against women, 2017+).

Build:  python -m safeher.ncrb   (reads data/raw/ncrb/districtwise-caw-2017-onwards.csv)
Source: https://ncrb.gov.in/crime-in-india.html via India Data Portal (CSV).

Only Delhi is usable at sub-city level: it reports 15 territorial police districts. Bengaluru is a
single "Bengaluru City" unit, so there is nothing to compare within the city (spec §7.4 requires
city-relative values) and the signal stays "no data" there.
"""

from __future__ import annotations

import csv
import json
import sys
from functools import lru_cache

from .areas import REPO_ROOT, Area

RAW_CSV = REPO_ROOT / "data" / "raw" / "ncrb" / "districtwise-caw-2017-onwards.csv"
OUT_JSON = REPO_ROOT / "data" / "incidents" / "ncrb_delhi_police_districts.json"
SOURCE_URL = ("https://ckandev.indiadataportal.com/dataset/crime-statistics/resource/"
              "c8d3ea7e-4855-45f3-9c26-557419e93b6a")

# Crime heads that relate to public spaces. Domestic heads (cruelty by husband, dowry, domestic
# violence) and cyber crimes are excluded: they say nothing about which street to walk down.
PUBLIC_SPACE_HEADS = (
    "assault_on_womenabove_18",
    "insult_to_the_modesty_of_women_above_18",
    "attempt_to_commit_rape_above_18",
    "kidnapping_and_abduction_of_women_others",
)
YEARS = ("2019", "2021", "2022")  # 2020 skipped: lockdown distorts counts
# Delhi Police units that are not geographic districts.
NON_TERRITORIAL = {"Crime Branch", "Economic Offences Wing", "Igi Airport", "Metro", "Railway",
                   "Spl Cell", "Spuwac", "Vigilance"}
CONFIDENCE = 0.15  # raw counts (no population), district-level, annual


def build() -> dict:
    rows = [r for r in csv.DictReader(RAW_CSV.open()) if r["state_name"] == "Delhi"]
    counts: dict[str, list[float]] = {}
    for r in rows:
        circle = r["registration_circles"]
        if r["year"] in YEARS and circle not in NON_TERRITORIAL:
            counts.setdefault(circle, []).append(sum(float(r[h] or 0) for h in PUBLIC_SPACE_HEADS))
    missing = {c: len(v) for c, v in counts.items() if len(v) != len(YEARS)}
    if missing:
        raise ValueError(f"districts missing years: {missing}")
    avg = {c: sum(v) / len(v) for c, v in counts.items()}
    ordered = sorted(avg.values())
    n = len(ordered)
    districts = {
        c: {
            "avg_cases_per_year": round(a, 1),
            # Share of other districts with fewer cases: 0 = fewest in Delhi, 1 = most.
            "city_percentile": round(sum(x < a for x in ordered) / (n - 1), 3),
        }
        for c, a in sorted(avg.items())
    }
    out = {
        "city": "Delhi",
        "unit": "Delhi Police territorial district (NCRB registration circle)",
        "years": list(YEARS),
        "heads": list(PUBLIC_SPACE_HEADS),
        "confidence": CONFIDENCE,
        "caveats": [
            "Raw registered case counts, not adjusted for population.",
            "Registered cases reflect reporting rates as well as incidence.",
            "One value per police district: does not distinguish streets within a district.",
        ],
        "source": "NCRB Crime in India, district-wise crimes against women (2017 onwards)",
        "source_url": SOURCE_URL,
        "districts": districts,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=2))
    return out


@lru_cache(maxsize=1)
def _table() -> dict | None:
    return json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else None


def lookup(area: Area) -> tuple[float, float, str] | None:
    """(city_percentile, confidence, note) for the area's police district, or None."""
    table = _table()
    if not area.ncrb_district or table is None or table["city"] != area.city:
        return None
    d = table["districts"].get(area.ncrb_district)
    if d is None:
        return None
    rank = sorted(table["districts"].values(), key=lambda x: x["avg_cases_per_year"]).index(d) + 1
    note = (f"{area.ncrb_district} district: {d['avg_cases_per_year']:.0f} public-space cases/year, "
            f"{rank} of {len(table['districts'])} {area.city} districts (1 = fewest)")
    return d["city_percentile"], table["confidence"], note


if __name__ == "__main__":
    if not RAW_CSV.exists():
        sys.exit(f"missing {RAW_CSV}\ndownload it from {SOURCE_URL}")
    result = build()
    for name, d in result["districts"].items():
        print(f"{name:<14} {d['avg_cases_per_year']:>6}  p={d['city_percentile']}")
    print(f"wrote {OUT_JSON}")
