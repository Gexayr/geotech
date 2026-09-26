"""Spatial post-processing for real, Marcaj-format annotations — same rules
as `services.metrics` (canopy area = polygon union, inter-row area = union
of interrow_area polygons, row length = axis polylines), but grouped by the
real `vineyard_id` attribute instead of one hardcoded demo block, since a
real tile can (and, per the annotation rules, does) hold more than one.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union


def compute_tile_metrics(tile: dict[str, Any]) -> list[dict[str, Any]]:
    vineyard_ids = sorted(
        {c.get("vineyard_id") for c in tile["canopies"]}
        | {r.get("vineyard_id") for r in tile["rows"]}
        | {i.get("vineyard_id") for i in tile["interrows"]}
    )

    blocks = []
    for vid in vineyard_ids:
        canopies = [c for c in tile["canopies"] if c.get("vineyard_id") == vid]
        rows = [r for r in tile["rows"] if r.get("vineyard_id") == vid]
        interrows = [i for i in tile["interrows"] if i.get("vineyard_id") == vid]

        canopy_area = (
            unary_union([Polygon(c["polygon"]) for c in canopies]).area if canopies else 0.0
        )
        interrow_area = (
            unary_union([Polygon(i["polygon"]) for i in interrows]).area if interrows else 0.0
        )
        row_lengths = {r["row_id"]: LineString(r["line"]).length for r in rows}

        blocks.append(
            {
                "vineyard_id": vid,
                "canopy_area_m2": round(canopy_area, 2),
                "canopy_area_ha": round(canopy_area / 10_000, 6),
                "interrow_area_m2": round(interrow_area, 2),
                "interrow_area_ha": round(interrow_area / 10_000, 6),
                "canopy_count": len(canopies),
                "row_count": len(row_lengths),
                "total_row_length_m": round(sum(row_lengths.values()), 2),
                "rows": [
                    {"row_id": rid, "length_m": round(length, 2)}
                    for rid, length in sorted(row_lengths.items())
                ],
            }
        )
    return blocks
