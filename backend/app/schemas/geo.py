"""Generic GeoJSON-ish schemas used to talk to the frontend.

Kept intentionally small (not a full GeoJSON spec implementation) so we
can later swap the data source for a real Marcaj export without touching
the API surface.
"""

from typing import Any, Literal
from pydantic import BaseModel


class Feature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any] = {}


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature]


class BlockSummary(BaseModel):
    id: str
    name: str
    extent_m2: float


class RowMeasurement(BaseModel):
    row_id: str
    length_m: float
    block_id: str


class MetricsResponse(BaseModel):
    block_id: str
    canopy_area_m2: float
    canopy_area_ha: float
    interrow_area_m2: float
    interrow_area_ha: float
    block_extent_m2: float
    row_count: int
    canopy_count: int
    total_row_length_m: float
    rows: list[RowMeasurement]


class RouteComparison(BaseModel):
    separate_trips_m: float
    optimized_tour_m: float
    saved_m: float
    reduction_pct: float


class CustomBlockCreate(BaseModel):
    vineyard_id: str
    polygon: list[list[float]]  # local-plane [x, y] ring, closed


class CustomRouteRequest(BaseModel):
    start: list[float] | None = None  # [x, y] local plane; omit = organizer's default
    tiles: list[str] | None = None  # subset of annotated tile names; omit = all loaded
    area: list[list[float]] | None = None  # hand-drawn polygon; filters targets to inside it


class RouteResponse(BaseModel):
    block_id: str
    start_id: str
    start_point: list[float]
    order: list[str]
    targets_visited: int
    targets_total: int
    length_m: float
    polyline: list[list[float]]
    comparison: RouteComparison
