"""NASA Black Marble (VIIRS) night-time lights as a lighting fallback (spec §7.2, §8).

Product: VNP46A4 — yearly, snow-free, near-nadir composite, ~500 m pixels. Downloaded by
`python -m safeher.data build` (needs EARTHDATA_TOKEN in backend/.env) and stored in the night_lights
table; store.night_lights() turns the rows back into a ViirsGrid.

VIIRS measures upward light, not street lamps: it separates dark parks/campuses from lit roads but
can't see one dark lane between lit buildings. It is used only where OSM has no `lit` tag and no
mapped lamps, at lower confidence than either.
"""

from __future__ import annotations

import os
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from .config import REPO_ROOT

PRODUCT = "VNP46A4"
RAW_DIR = REPO_ROOT / "data" / "raw" / "viirs"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass
class ViirsGrid:
    """Radiance on a regular lat/lon grid, with pixel-centre coordinates."""

    radiance: np.ndarray  # shape (len(lats), len(lons)); NaN = no valid observation
    lats: np.ndarray  # pixel-centre latitudes (any order)
    lons: np.ndarray  # pixel-centre longitudes (ascending)
    year: int

    def __post_init__(self) -> None:
        valid = self.radiance[np.isfinite(self.radiance)]
        if valid.size == 0:
            raise ValueError("VIIRS grid has no valid pixels")
        self._sorted = np.sort(valid)
        self._dlat = abs(float(self.lats[1] - self.lats[0])) if len(self.lats) > 1 else 0.005
        self._dlon = abs(float(self.lons[1] - self.lons[0])) if len(self.lons) > 1 else 0.005

    def radiance_at(self, lat: float, lon: float) -> float | None:
        i = int(np.argmin(np.abs(self.lats - lat)))
        j = int(np.argmin(np.abs(self.lons - lon)))
        if abs(self.lats[i] - lat) > self._dlat / 2 + 1e-9 or abs(self.lons[j] - lon) > self._dlon / 2 + 1e-9:
            return None  # outside the grid
        v = float(self.radiance[i, j])
        return v if np.isfinite(v) else None

    def percentile_at(self, lat: float, lon: float) -> float | None:
        """Share of the area's pixels darker than this one (0 = darkest, 1 = brightest)."""
        v = self.radiance_at(lat, lon)
        if v is None:
            return None
        n = len(self._sorted)
        return bisect_left(self._sorted.tolist(), v) / (n - 1) if n > 1 else 0.5


# --- Fetching -------------------------------------------------------------------------------


def _token() -> str:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    token = os.environ.get("EARTHDATA_TOKEN", "").strip()
    if not token or token.startswith("paste-"):
        raise RuntimeError(f"EARTHDATA_TOKEN missing: add it to {ENV_FILE} (see https://urs.earthdata.nasa.gov)")
    return token


def fetch_bbox(south: float, west: float, north: float, east: float, year: int) -> ViirsGrid:
    """Download the yearly composite for a bbox (cached by blackmarblepy under data/raw/viirs)."""
    import geopandas as gpd
    from blackmarble import BlackMarble
    from shapely.geometry import box

    roi = gpd.GeoDataFrame(geometry=[box(west, south, east, north)], crs="EPSG:4326")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    bm = BlackMarble(token=_token(), output_directory=RAW_DIR)
    ds = bm.raster(roi, product_id=PRODUCT, date_range=date(year, 1, 1))
    var = next(iter(ds.data_vars))
    da = ds[var].squeeze(drop=True)
    lat_name = next(d for d in da.dims if d in ("y", "lat", "latitude"))
    lon_name = next(d for d in da.dims if d in ("x", "lon", "longitude"))
    da = da.transpose(lat_name, lon_name).sortby(lon_name)
    return ViirsGrid(da.values.astype(float), da[lat_name].values, da[lon_name].values, year)
