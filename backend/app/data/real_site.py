"""Real organizer-supplied route infrastructure for Sireț3 (EPSG:32635).

Loads start/passages/forbidden/study_area exactly as released by Marcaj —
no synthetic geometry. Coordinates are re-expressed on a local metre plane
(subtracting ORIGIN, the study area's south-west corner) purely so the
frontend can render them with Leaflet's CRS.Simple without hauling six-digit
UTM numbers around; `to_world()` recovers real EPSG:32635 for anything that
must be exported (route.geojson, measurements.csv).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).parent / "real" / "route"
SOURCE_CRS = "EPSG:32635"


def _load(name: str) -> dict[str, Any]:
    with open(ASSETS_DIR / name, encoding="utf-8") as f:
        return json.load(f)


_study_area_raw = _load("study_area.geojson")
_passages_raw = _load("passages.geojson")
_forbidden_raw = _load("forbidden.geojson")
_start_raw = _load("start.geojson")


def _bounds_of(geojson: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords: Any) -> None:
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            xs.append(coords[0])
            ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    for feature in geojson["features"]:
        walk(feature["geometry"]["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


_minx, _miny, _maxx, _maxy = _bounds_of(_study_area_raw)
ORIGIN = (_minx, _miny)  # world EPSG:32635 coords of local (0, 0)


def to_local(x: float, y: float) -> tuple[float, float]:
    return (x - ORIGIN[0], y - ORIGIN[1])


def to_world(x: float, y: float) -> tuple[float, float]:
    return (x + ORIGIN[0], y + ORIGIN[1])


def _offset_coords(coords: Any) -> Any:
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        return list(to_local(coords[0], coords[1]))
    return [_offset_coords(c) for c in coords]


def _offset_feature_collection(geojson: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": f["geometry"]["type"],
                    "coordinates": _offset_coords(f["geometry"]["coordinates"]),
                },
                "properties": f.get("properties", {}),
            }
            for f in geojson["features"]
        ],
    }


def get_study_area_local() -> dict[str, Any]:
    return _offset_feature_collection(_study_area_raw)


def get_passages_local() -> dict[str, Any]:
    return _offset_feature_collection(_passages_raw)


def get_forbidden_local() -> dict[str, Any]:
    return _offset_feature_collection(_forbidden_raw)


def get_start_local() -> dict[str, Any]:
    return _offset_feature_collection(_start_raw)


def get_start_point_local() -> tuple[float, float]:
    coords = _start_raw["features"][0]["geometry"]["coordinates"]
    return to_local(coords[0], coords[1])


def get_extent_local() -> tuple[float, float]:
    """Local (width, height) of the study area bounding box, in metres."""
    return (_maxx - _minx, _maxy - _miny)


# --- shapely accessors (for rasterization / pathfinding) ------------------


def _to_shapely_union(feature_collection: dict) -> Any:
    from shapely.geometry import shape
    from shapely.ops import unary_union

    geoms = [shape(f["geometry"]) for f in feature_collection["features"]]
    return unary_union(geoms) if geoms else None


def get_passages_shapely_local() -> Any:
    return _to_shapely_union(get_passages_local())


def get_forbidden_shapely_local() -> Any:
    return _to_shapely_union(get_forbidden_local())
