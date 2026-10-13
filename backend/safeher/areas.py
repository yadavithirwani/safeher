"""Demo areas. Each is a bounding box small enough to cache, in one of the pilot cities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "areas"


@dataclass(frozen=True)
class Area:
    id: str
    city: str
    name: str
    south: float
    west: float
    north: float
    east: float
    timezone: str = "Asia/Kolkata"
    ncrb_district: str | None = None  # NCRB registration circle covering the area, if sub-city data exists

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) — the order OSMnx 2.x expects."""
        return (self.west, self.south, self.east, self.north)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def dir(self) -> Path:
        return DATA_DIR / self.id

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


AREAS = {
    a.id: a
    for a in (
        Area("blr-indiranagar-mg-road", "Bengaluru", "Indiranagar – MG Road",
             south=12.962, west=77.600, north=12.988, east=77.648),
        Area("del-hauz-khas-saket", "Delhi", "Hauz Khas – Saket",
             south=28.518, west=77.195, north=28.556, east=77.230, ncrb_district="South"),
    )
}


def area_for(lat: float, lon: float) -> Area | None:
    return next((a for a in AREAS.values() if a.contains(lat, lon)), None)
