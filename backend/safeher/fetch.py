"""CLI: python -m safeher.fetch [area-id ...]  (no args = all demo areas)."""

import sys

from .areas import AREAS
from .osm import save_area


def main(argv: list[str]) -> int:
    ids = argv or list(AREAS)
    for area_id in ids:
        if area_id not in AREAS:
            print(f"unknown area {area_id!r}; choose from {', '.join(AREAS)}", file=sys.stderr)
            return 2
        print(f"fetching {area_id} …", flush=True)
        print(f"  {save_area(AREAS[area_id])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
