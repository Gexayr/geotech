"""Real-geometry walking-route pathfinding.

Walkable = organizer-authorised passages UNION the annotated inter-row
areas, MINUS forbidden zones and canopies — exactly the brief's constraint
("Use passable inter-row areas and authorised passages, not paths through
canopies, fences or forbidden areas"). Rasterizes that real geometry onto a
grid (rasterio.features.rasterize) and runs A* on it — simple, dependency-light,
and fine for a handful of start/target queries; swap for a proper navmesh
or a compiled grid-graph library if the target count grows large.
"""

from __future__ import annotations

import heapq
import math
from typing import Any

import numpy as np
import rasterio.features
import rasterio.transform
from shapely.geometry import Polygon
from shapely.ops import unary_union

from app.data import real_site

RESOLUTION_M = 2.0


class WalkableGrid:
    def __init__(self, extra_walkable: list[Any], extra_blocked: list[Any]):
        extent_x, extent_y = real_site.get_extent_local()
        self.width = math.ceil(extent_x / RESOLUTION_M)
        self.height = math.ceil(extent_y / RESOLUTION_M)
        self.transform = rasterio.transform.from_origin(
            0, self.height * RESOLUTION_M, RESOLUTION_M, RESOLUTION_M
        )

        passages = real_site.get_passages_shapely_local()
        forbidden = real_site.get_forbidden_shapely_local()

        allowed_geoms = [g for g in [passages, *extra_walkable] if g is not None]
        blocked_geoms = [g for g in [forbidden, *extra_blocked] if g is not None]

        self.walkable = self._rasterize(allowed_geoms)
        blocked = self._rasterize(blocked_geoms)
        self.walkable &= ~blocked

    def _rasterize(self, geoms: list[Any]) -> np.ndarray:
        flat = []
        for g in geoms:
            flat.extend(list(g.geoms) if hasattr(g, "geoms") else [g])
        if not flat:
            return np.zeros((self.height, self.width), dtype=bool)
        arr = rasterio.features.rasterize(
            [(g, 1) for g in flat],
            out_shape=(self.height, self.width),
            transform=self.transform,
            fill=0,
            dtype="uint8",
        )
        return arr.astype(bool)

    def to_rc(self, x: float, y: float) -> tuple[int, int]:
        row, col = rasterio.transform.rowcol(self.transform, x, y)
        return int(row), int(col)

    def to_xy(self, row: int, col: int) -> tuple[float, float]:
        x, y = rasterio.transform.xy(self.transform, row, col)
        return float(x), float(y)

    def nearest_walkable(self, x: float, y: float, max_radius_cells: int = 15) -> tuple[int, int]:
        row, col = self.to_rc(x, y)
        if 0 <= row < self.height and 0 <= col < self.width and self.walkable[row, col]:
            return row, col
        for r in range(1, max_radius_cells + 1):
            for dr in range(-r, r + 1):
                for dc in (-r, r):
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < self.height and 0 <= nc < self.width and self.walkable[nr, nc]:
                        return nr, nc
            for dc in range(-r, r + 1):
                for dr in (-r, r):
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < self.height and 0 <= nc < self.width and self.walkable[nr, nc]:
                        return nr, nc
        raise ValueError("No walkable cell found near point — check passages data / point location")


_NEIGHBORS = [
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2)),
]


def astar(grid: WalkableGrid, start_rc: tuple[int, int], goal_rc: tuple[int, int]) -> list[tuple[int, int]] | None:
    if start_rc == goal_rc:
        return [start_rc]

    def h(a: tuple[int, int], b: tuple[int, int]) -> float:
        return RESOLUTION_M * math.dist(a, b)

    open_heap: list[tuple[float, tuple[int, int]]] = [(0.0, start_rc)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score = {start_rc: 0.0}
    visited: set[tuple[int, int]] = set()
    h_, w_ = grid.height, grid.width

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current in visited:
            continue
        visited.add(current)
        if current == goal_rc:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        cr, cc = current
        for dr, dc, step_cost in _NEIGHBORS:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < h_ and 0 <= nc < w_ and grid.walkable[nr, nc]:
                neighbor = (nr, nc)
                tentative = g_score[current] + step_cost * RESOLUTION_M
                if tentative < g_score.get(neighbor, math.inf):
                    g_score[neighbor] = tentative
                    came_from[neighbor] = current
                    heapq.heappush(open_heap, (tentative + h(neighbor, goal_rc), neighbor))
    return None


def path_length_m(path_xy: list[tuple[float, float]]) -> float:
    return sum(math.dist(path_xy[i], path_xy[i + 1]) for i in range(len(path_xy) - 1))


def build_grid_for_tiles(tiles: dict[str, dict[str, Any]]) -> WalkableGrid:
    """Inter-row polygons from every currently loaded (annotated) tile count
    as walkable too, and their canopy polygons as blocked — real per-tile
    detail layered on top of the site-wide passages/forbidden network."""
    interrow_polys = []
    canopy_polys = []
    for t in tiles.values():
        interrow_polys += [Polygon(i["polygon"]) for i in t["interrows"]]
        canopy_polys += [Polygon(c["polygon"]) for c in t["canopies"]]

    extra_walkable = [unary_union(interrow_polys)] if interrow_polys else []
    extra_blocked = [unary_union(canopy_polys)] if canopy_polys else []
    return WalkableGrid(extra_walkable, extra_blocked)
