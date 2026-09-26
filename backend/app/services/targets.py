"""Derives walking-route targets from the real annotations.

Per the challenge brief, inspection locations are NOT annotated in Marcaj —
they are our own application's output: "Identify locations requiring
inspection, such as visible row gaps or potentially missing planting...
They, together with detected waste, are the targets of the walking route."

So: a `row_structure = disrupted` row (a 5m+ gap) becomes an inspection
target at its midpoint, and every `waste` box becomes a waste-collection
target at its centroid. Correctly returns an empty list while the loaded
annotations have neither (true of the 2 example tiles today) — this isn't a
placeholder, it's the real answer for the data currently on disk.
"""

from __future__ import annotations

from typing import Any


def _polyline_midpoint(line: list[list[float]]) -> tuple[float, float]:
    # Midpoint by cumulative length, not just the middle vertex — a gap can
    # sit anywhere along a multi-point row axis.
    seg_lengths = []
    total = 0.0
    for i in range(len(line) - 1):
        (x0, y0), (x1, y1) = line[i], line[i + 1]
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        seg_lengths.append(d)
        total += d
    if total == 0:
        return tuple(line[0])
    half = total / 2
    acc = 0.0
    for i, d in enumerate(seg_lengths):
        if acc + d >= half:
            t = (half - acc) / d if d else 0
            (x0, y0), (x1, y1) = line[i], line[i + 1]
            return (x0 + t * (x1 - x0), y0 + t * (y1 - y0))
        acc += d
    return tuple(line[-1])


def _bbox_centroid(ring: list[list[float]]) -> tuple[float, float]:
    xs = [p[0] for p in ring[:4]]
    ys = [p[1] for p in ring[:4]]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def derive_targets(tiles: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []

    for tile_name, t in tiles.items():
        for r in t["rows"]:
            if r.get("row_structure") == "disrupted":
                point = _polyline_midpoint(r["line"])
                targets.append(
                    {
                        "id": f"gap-{r.get('row_id')}",
                        "kind": "inspection",
                        "point": list(point),
                        "vineyard_id": r.get("vineyard_id"),
                        "row_id": r.get("row_id"),
                        "source_tile": tile_name,
                    }
                )

        for i, w in enumerate(t["waste"]):
            point = _bbox_centroid(w["bbox"])
            targets.append(
                {
                    "id": f"waste-{tile_name}-{i}",
                    "kind": "waste",
                    "point": list(point),
                    "vineyard_id": w.get("vineyard_id"),
                    "row_id": None,
                    "source_tile": tile_name,
                }
            )

    return targets
