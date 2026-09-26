"""Cross-tile measurement aggregation — the actual submission deliverable
(`measurements.csv`: "block and row counts, row lengths and areas by
vineyard_id/row_id").

Unlike `real_metrics` (per single tile, used by the tile inspector view),
this flattens every loaded tile first: a block or row that crosses a tile
edge is drawn as several objects sharing one vineyard_id/row_id (per the
annotation rules), so row length must SUM across pieces and canopy/inter-row
area must be the polygon UNION across every tile, not computed tile-by-tile.
Written to work today on the 2 example tiles and unchanged once the full
311-tile Marcaj export lands — same function, more input.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union


def _flatten(tiles: dict[str, dict[str, Any]]) -> tuple[list, list, list, list]:
    canopies: list[dict] = []
    rows: list[dict] = []
    interrows: list[dict] = []
    waste: list[dict] = []
    for t in tiles.values():
        canopies += t["canopies"]
        rows += t["rows"]
        interrows += t["interrows"]
        waste += t["waste"]
    return canopies, rows, interrows, waste


def compute_measurements(tiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    canopies, rows, interrows, waste = _flatten(tiles)

    vineyard_ids = sorted(
        {c.get("vineyard_id") for c in canopies}
        | {r.get("vineyard_id") for r in rows}
        | {i.get("vineyard_id") for i in interrows}
    )

    blocks = []
    total_canopy_area = 0.0
    total_interrow_area = 0.0
    total_row_length = 0.0
    total_row_count = 0

    for vid in vineyard_ids:
        v_canopies = [c for c in canopies if c.get("vineyard_id") == vid]
        v_interrows = [i for i in interrows if i.get("vineyard_id") == vid]
        v_rows = [r for r in rows if r.get("vineyard_id") == vid]

        canopy_area = (
            unary_union([Polygon(c["polygon"]) for c in v_canopies]).area if v_canopies else 0.0
        )
        interrow_area = (
            unary_union([Polygon(i["polygon"]) for i in v_interrows]).area
            if v_interrows
            else 0.0
        )

        # A row split across tiles is several polylines sharing one row_id —
        # sum their lengths rather than treating each piece as its own row.
        row_lengths: dict[str, float] = {}
        for r in v_rows:
            rid = r.get("row_id")
            row_lengths[rid] = row_lengths.get(rid, 0.0) + LineString(r["line"]).length

        block_row_total = sum(row_lengths.values())

        blocks.append(
            {
                "vineyard_id": vid,
                "canopy_area_m2": round(canopy_area, 2),
                "canopy_area_ha": round(canopy_area / 10_000, 6),
                "interrow_area_m2": round(interrow_area, 2),
                "interrow_area_ha": round(interrow_area / 10_000, 6),
                "canopy_count": len(v_canopies),
                "row_count": len(row_lengths),
                "total_row_length_m": round(block_row_total, 2),
                "rows": [
                    {"row_id": rid, "length_m": round(length, 2)}
                    for rid, length in sorted(row_lengths.items())
                ],
            }
        )

        total_canopy_area += canopy_area
        total_interrow_area += interrow_area
        total_row_length += block_row_total
        total_row_count += len(row_lengths)

    return {
        "block_count": len(vineyard_ids),
        "row_count": total_row_count,
        "canopy_area_m2": round(total_canopy_area, 2),
        "canopy_area_ha": round(total_canopy_area / 10_000, 6),
        "interrow_area_m2": round(total_interrow_area, 2),
        "interrow_area_ha": round(total_interrow_area / 10_000, 6),
        "total_row_length_m": round(total_row_length, 2),
        "waste_count": len(waste),
        "tiles_loaded": len(tiles),
        "blocks": blocks,
    }


def to_csv_rows(measurements: dict[str, Any]) -> list[dict[str, Any]]:
    """Long/tidy format: one row per physical row, one per block summary, one
    grand total — filterable by `level` rather than three separate files."""
    out: list[dict[str, Any]] = []
    for b in measurements["blocks"]:
        for r in b["rows"]:
            out.append(
                {
                    "level": "row",
                    "vineyard_id": b["vineyard_id"],
                    "row_id": r["row_id"],
                    "row_length_m": r["length_m"],
                    "canopy_area_m2": "",
                    "interrow_area_m2": "",
                    "canopy_count": "",
                    "row_count": "",
                    "block_count": "",
                }
            )
        out.append(
            {
                "level": "block",
                "vineyard_id": b["vineyard_id"],
                "row_id": "",
                "row_length_m": b["total_row_length_m"],
                "canopy_area_m2": b["canopy_area_m2"],
                "interrow_area_m2": b["interrow_area_m2"],
                "canopy_count": b["canopy_count"],
                "row_count": b["row_count"],
                "block_count": "",
            }
        )
    out.append(
        {
            "level": "total",
            "vineyard_id": "",
            "row_id": "",
            "row_length_m": measurements["total_row_length_m"],
            "canopy_area_m2": measurements["canopy_area_m2"],
            "interrow_area_m2": measurements["interrow_area_m2"],
            "canopy_count": "",
            "row_count": measurements["row_count"],
            "block_count": measurements["block_count"],
        }
    )
    return out


CSV_FIELDS = [
    "level",
    "vineyard_id",
    "row_id",
    "row_length_m",
    "canopy_area_m2",
    "interrow_area_m2",
    "canopy_count",
    "row_count",
    "block_count",
]
