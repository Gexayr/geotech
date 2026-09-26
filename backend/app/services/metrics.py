"""Spatial post-processing: counts, lengths and areas from annotation geometry.

Mirrors the challenge brief's calculation rules:
- Canopy area: polygon union (so overlapping crowns aren't double-counted).
- Inter-row area: passage polygons.
- Row length: axis polylines.
"""

from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union

from app.data import demo_v001 as demo
from app.schemas.geo import MetricsResponse, RowMeasurement


def _canopy_union_area_m2() -> float:
    polys = [Polygon(c["polygon"]) for c in demo.CANOPIES]
    return unary_union(polys).area


def _interrow_area_m2() -> float:
    polys = [Polygon(ir["polygon"]) for ir in demo.INTERROWS]
    return unary_union(polys).area


def _row_lengths_m() -> dict[str, float]:
    return {r["row_id"]: LineString(r["line"]).length for r in demo.ROWS}


def _block_extent_m2() -> float:
    return Polygon(demo.BLOCK_POLYGON).area


def compute_metrics(block_id: str) -> MetricsResponse:
    row_lengths = _row_lengths_m()
    canopy_area = _canopy_union_area_m2()
    interrow_area = _interrow_area_m2()

    return MetricsResponse(
        block_id=block_id,
        canopy_area_m2=round(canopy_area, 2),
        canopy_area_ha=round(canopy_area / 10_000, 6),
        interrow_area_m2=round(interrow_area, 2),
        interrow_area_ha=round(interrow_area / 10_000, 6),
        block_extent_m2=round(_block_extent_m2(), 2),
        row_count=len(demo.ROWS),
        canopy_count=len(demo.CANOPIES),
        total_row_length_m=round(sum(row_lengths.values()), 2),
        rows=[
            RowMeasurement(row_id=row_id, length_m=round(length, 2), block_id=block_id)
            for row_id, length in sorted(row_lengths.items())
        ],
    )
