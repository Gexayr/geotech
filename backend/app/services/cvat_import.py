"""Parses Marcaj's real CVAT for Images 1.1 export into our internal
per-tile feature representation.

This is the actual annotation interchange format used by the challenge
(confirmed by the organizers' supplied example export and the annotation
rules PDF) — not a guessed schema. Pixel coordinates are converted to
world coordinates (EPSG:32635) via each tile's real GeoTIFF geotransform,
then onto the local display plane shared with the route infrastructure
(`app.data.real_site`), so tiles and passages/forbidden zones line up on
one map.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.data import real_site
from app.services import georef

GROUND_TRUTH_PATH = Path(__file__).parent.parent / "data" / "real" / "examples" / "annotations.xml"
FALLBACK_PATH = Path(__file__).parent.parent / "data" / "real" / "auto_annotations" / "annotations.xml"
XML_PATH = GROUND_TRUTH_PATH  # kept for backward compatibility


def _parse_points(raw: str) -> list[tuple[float, float]]:
    pts = []
    for pair in raw.strip().split(";"):
        x_str, y_str = pair.split(",")
        pts.append((float(x_str), float(y_str)))
    return pts


def _attrs(el: ET.Element) -> dict[str, str]:
    return {a.get("name"): (a.text or "") for a in el.findall("attribute")}


def _pixel_ring_to_local(tile_name: str, pts: list[tuple[float, float]]) -> list[list[float]]:
    ring = []
    for px, py in pts:
        wx, wy = georef.pixel_to_world(tile_name, px, py)
        lx, ly = real_site.to_local(wx, wy)
        ring.append([lx, ly])
    return ring


def tile_bounds_local(tile_name: str) -> tuple[float, float, float, float]:
    left, bottom, right, top = georef.tile_world_bounds(tile_name)
    x0, y0 = real_site.to_local(left, bottom)
    x1, y1 = real_site.to_local(right, top)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def from_pixel_detection(tile_name: str, pixel_result: dict, source: str) -> dict[str, Any]:
    """Converts app.services.classical_cv.process_tile()'s pixel-space
    output into our internal per-tile format (local-plane coords) — the
    live counterpart to _parse_cvat_file, for a tile detected on the fly
    (upload) rather than parsed from a CVAT XML on disk."""
    canopies = []
    for c in pixel_result["canopies"]:
        ring = _pixel_ring_to_local(tile_name, c["points"])
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        canopies.append({"polygon": ring, "vineyard_id": c["vineyard_id"]})

    rows = []
    for r in pixel_result["rows"]:
        rows.append(
            {
                "line": _pixel_ring_to_local(tile_name, r["points"]),
                "vineyard_id": r["vineyard_id"],
                "row_id": r["row_id"],
                "row_structure": r["row_structure"],
            }
        )

    interrows = []
    for ir in pixel_result["interrows"]:
        ring = _pixel_ring_to_local(tile_name, ir["points"])
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        interrows.append(
            {
                "polygon": ring,
                "vineyard_id": ir["vineyard_id"],
                "interrow_cover": ir["interrow_cover"],
            }
        )

    return {
        "canopies": canopies,
        "rows": rows,
        "interrows": interrows,
        "waste": [],
        "bounds": tile_bounds_local(tile_name),
        "source": source,
    }


def _parse_cvat_file(xml_path: Path, source: str) -> dict[str, dict[str, Any]]:
    """Returns {tile_name: {canopies, rows, interrows, waste, bounds, source}}.
    `source` tags every tile from this file so callers (and the UI) can tell
    real, human-reviewed annotations apart from a fallback."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    tiles: dict[str, dict[str, Any]] = {}

    for image_el in root.findall("image"):
        tile_name = image_el.get("name")
        canopies: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        interrows: list[dict[str, Any]] = []
        waste: list[dict[str, Any]] = []

        for poly in image_el.findall("polygon"):
            label = poly.get("label")
            pts = _parse_points(poly.get("points"))
            ring = _pixel_ring_to_local(tile_name, pts)
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            attrs = _attrs(poly)
            if label == "vineyard":
                canopies.append({"polygon": ring, **attrs})
            elif label == "interrow_area":
                interrows.append({"polygon": ring, **attrs})

        for line in image_el.findall("polyline"):
            if line.get("label") != "row":
                continue
            pts = _parse_points(line.get("points"))
            rows.append({"line": _pixel_ring_to_local(tile_name, pts), **_attrs(line)})

        for box in image_el.findall("box"):
            if box.get("label") != "waste":
                continue
            xtl, ytl = float(box.get("xtl")), float(box.get("ytl"))
            xbr, ybr = float(box.get("xbr")), float(box.get("ybr"))
            ring = _pixel_ring_to_local(
                tile_name, [(xtl, ytl), (xbr, ytl), (xbr, ybr), (xtl, ybr), (xtl, ytl)]
            )
            waste.append({"bbox": ring, **_attrs(box)})

        tiles[tile_name] = {
            "canopies": canopies,
            "rows": rows,
            "interrows": interrows,
            "waste": waste,
            "bounds": tile_bounds_local(tile_name),
            "source": source,
        }

    return tiles


def load_examples() -> dict[str, dict[str, Any]]:
    """The 2 organizer-supplied, human-reviewed reference tiles only."""
    return _parse_cvat_file(GROUND_TRUTH_PATH, source="ground_truth")


def load_merged() -> dict[str, dict[str, Any]]:
    """Ground truth where we have it (2 tiles), a classical-CV fallback
    (no trained model, see scripts/classical_pre_annotate.py) for the rest —
    real coverage across every tile that looks like vineyard rows, while the
    2 known-good tiles stay authoritative. Swap in the team's actual model
    export by pointing FALLBACK_PATH (or a new source) here once it lands."""
    tiles = {}
    if FALLBACK_PATH.exists():
        tiles.update(_parse_cvat_file(FALLBACK_PATH, source="classical_cv"))
    tiles.update(_parse_cvat_file(GROUND_TRUTH_PATH, source="ground_truth"))
    return tiles
