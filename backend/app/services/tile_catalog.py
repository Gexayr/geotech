"""Catalog of all 311 organizer-supplied tiles — independent of which ones
happen to have annotations. Reads each GeoTIFF's header only (bounds via
its embedded geotransform, not pixel data), so scanning the full set is
fast; cached after the first call.
"""

from __future__ import annotations

from pathlib import Path

import rasterio

from app.data import real_site

TILES_DIR = Path(__file__).parent.parent / "data" / "real" / "tiles"

_catalog_cache: dict[str, tuple[float, float, float, float]] | None = None


def _scan() -> dict[str, tuple[float, float, float, float]]:
    catalog: dict[str, tuple[float, float, float, float]] = {}
    for path in sorted(TILES_DIR.glob("*.tif")):
        with rasterio.open(path) as ds:
            b = ds.bounds
        x0, y0 = real_site.to_local(b.left, b.bottom)
        x1, y1 = real_site.to_local(b.right, b.top)
        catalog[path.name] = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return catalog


def get_catalog() -> dict[str, tuple[float, float, float, float]]:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = _scan()
    return _catalog_cache


def tile_exists(name: str) -> bool:
    return name in get_catalog()
