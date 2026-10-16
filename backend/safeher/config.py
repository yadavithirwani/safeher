"""Load SafeHer's configuration from the repo's config/ folder.

- config/cities.json   supported cities, their data sources, landmarks and default trips
- config/app.json      default city/departure, trip limits, helplines, basemaps, languages
- config/scoring.json  the published, versioned scoring model

SAFEHER_CONFIG_DIR points at a different folder (e.g. for tests or another deployment).
Machine-specific values (API keys, model names, URLs) come from environment variables / backend/.env.
"""

from __future__ import annotations

import json
import os
from datetime import time
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return Path(os.environ.get("SAFEHER_CONFIG_DIR", REPO_ROOT / "config"))


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    path = config_dir() / f"{name}.json"
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as e:
        raise RuntimeError(f"missing config file {path}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"invalid JSON in {path}: {e}") from e


def scoring() -> dict:
    return load("scoring")


def app() -> dict:
    return load("app")



def hhmm(value: str) -> time:
    h, m = (int(x) for x in value.split(":"))
    return time(h, m)
