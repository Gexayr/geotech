"""Pixel -> world coordinate transforms for the real GeoTIFF tiles.

Marcaj annotators draw in pixel space (per the annotation rules: "Annotate
in pixels in Marcaj — the georeferencing is kept by the platform, and
scoring converts to metres"). We do the same conversion here, reading each
tile's real affine transform straight out of its GeoTIFF.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

TILES_DIR = Path(__file__).parent.parent / "data" / "real" / "tiles"
PNG_CACHE_DIR = Path(__file__).parent.parent / "data" / "real" / "_jpeg_cache"
PNG_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_transform_cache: dict[str, "rasterio.Affine"] = {}
_bounds_cache: dict[str, tuple[float, float, float, float]] = {}


def _dataset_info(tile_name: str) -> tuple["rasterio.Affine", tuple[float, float, float, float]]:
    if tile_name not in _transform_cache:
        with rasterio.open(TILES_DIR / tile_name) as ds:
            _transform_cache[tile_name] = ds.transform
            b = ds.bounds
            _bounds_cache[tile_name] = (b.left, b.bottom, b.right, b.top)
    return _transform_cache[tile_name], _bounds_cache[tile_name]


def pixel_to_world(tile_name: str, px: float, py: float) -> tuple[float, float]:
    transform, _ = _dataset_info(tile_name)
    x, y = transform * (px, py)
    return x, y


def tile_world_bounds(tile_name: str) -> tuple[float, float, float, float]:
    """Returns (left, bottom, right, top) in the tile's native CRS (EPSG:32635)."""
    _, bounds = _dataset_info(tile_name)
    return bounds


def tile_path(tile_name: str) -> Path:
    return TILES_DIR / tile_name


def tile_to_jpeg(tile_name: str, quality: int = 87) -> bytes:
    """Renders the real GeoTIFF tile to JPEG (RGB) for the browser, which
    can't display GeoTIFF directly. JPEG rather than PNG: this is a
    photographic orthophoto, so lossy compression cuts an ~8MB PNG to a few
    hundred KB with no visible loss — much faster to load as a map
    background. Cached to disk since it's a pure function of the
    (unchanging) source file."""
    cache_path = PNG_CACHE_DIR / f"{tile_name}.jpg"
    if cache_path.exists():
        return cache_path.read_bytes()

    path = tile_path(tile_name)
    if not path.exists():
        raise FileNotFoundError(tile_name)

    with rasterio.open(path) as ds:
        band_count = min(ds.count, 3)
        arr = ds.read(list(range(1, band_count + 1)))  # (bands, H, W)

    arr = np.transpose(arr, (1, 2, 0))  # -> (H, W, bands)
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)

    image = Image.fromarray(arr.astype(np.uint8), mode="RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True)
    data = buf.getvalue()
    cache_path.write_bytes(data)
    return data
