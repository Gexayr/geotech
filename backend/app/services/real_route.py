"""Ties together real targets (targets.py) and real pathfinding
(pathfinding.py) into the actual walking route: a closed tour from the
organizer's start point visiting every derived target, over the real
passages/forbidden/inter-row network.

Exact permutation search for small target counts (matches the old demo's
approach, now over real geometry); nearest-neighbour for larger sets, since
brute force stops being feasible past ~8 stops.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from shapely.geometry import Point, Polygon

from app.data import real_site
from app.services import pathfinding
from app.services import targets as targets_svc

EXACT_TSP_LIMIT = 8

# A single tour across every derived target on the whole ~145ha site isn't a
# realistic walking route anyway (nobody inspects the entire farm in one
# loop), and the pairwise-A* distance matrix is O(n^2) — past this count it
# stops being a few seconds and starts being unusable. Scope down via
# /route/custom (tiles= or area=) for a real per-block route instead.
MAX_TARGETS_FOR_ROUTE = 40


def _pairwise_paths(
    grid: pathfinding.WalkableGrid, nodes: list[tuple[int, int]]
) -> tuple[list[list[float]], dict[tuple[int, int], list | None]]:
    n = len(nodes)
    dist = [[0.0] * n for _ in range(n)]
    paths: dict[tuple[int, int], list | None] = {}
    for i in range(n):
        for j in range(i + 1, n):
            path_rc = pathfinding.astar(grid, nodes[i], nodes[j])
            d = math.inf
            if path_rc is not None:
                path_xy = [grid.to_xy(r, c) for r, c in path_rc]
                d = pathfinding.path_length_m(path_xy)
            dist[i][j] = dist[j][i] = d
            paths[(i, j)] = path_rc
            paths[(j, i)] = list(reversed(path_rc)) if path_rc else None
    return dist, paths


def _best_order(dist: list[list[float]], n_targets: int) -> tuple[list[int], float]:
    target_indices = list(range(1, n_targets + 1))

    if n_targets <= EXACT_TSP_LIMIT:
        best_order: list[int] = []
        best_len = math.inf
        for perm in itertools.permutations(target_indices):
            route = [0, *perm, 0]
            total = sum(dist[route[k]][route[k + 1]] for k in range(len(route) - 1))
            if total < best_len:
                best_len = total
                best_order = list(perm)
        return best_order, best_len

    # Nearest-neighbour fallback for larger target counts.
    unvisited = set(target_indices)
    order: list[int] = []
    current = 0
    total = 0.0
    while unvisited:
        nxt = min(unvisited, key=lambda j: dist[current][j])
        total += dist[current][nxt]
        order.append(nxt)
        unvisited.remove(nxt)
        current = nxt
    total += dist[current][0]
    return order, total


def compute_real_route(
    tiles: dict[str, Any],
    start_point: tuple[float, float] | None = None,
    area_polygon: list[list[float]] | None = None,
) -> dict[str, Any]:
    """`tiles` is already the operator's scope (e.g. only the tiles they
    multi-selected) — filtering by tile happens in the API layer, since the
    same walkable-grid construction (interrow areas add walkable ground,
    canopies block it) must also only see the scoped tiles. `start_point`
    overrides the organizer's default start; `area_polygon` (a hand-drawn
    area) further restricts targets to those falling inside it.
    """
    target_list = targets_svc.derive_targets(tiles)

    if area_polygon:
        scope = Polygon(area_polygon)
        target_list = [t for t in target_list if scope.contains(Point(t["point"]))]

    start_xy = start_point if start_point is not None else real_site.get_start_point_local()

    if not target_list:
        return {
            "targets": [],
            "route": None,
            "message": (
                "No inspection/waste targets in the currently loaded annotations — "
                "the route computes automatically once disrupted rows or waste appear."
            ),
        }

    if len(target_list) > MAX_TARGETS_FOR_ROUTE:
        return {
            "targets": target_list,
            "route": None,
            "message": (
                f"{len(target_list)} targets found across the current scope — too many for "
                f"one walking tour (and O(n²) pathfinding stops being practical past "
                f"{MAX_TARGETS_FOR_ROUTE}). Scope down to specific tiles or a drawn area in "
                "the route planner to route one section at a time."
            ),
        }

    grid = pathfinding.build_grid_for_tiles(tiles)
    start_rc = grid.nearest_walkable(*start_xy)
    target_rcs = [grid.nearest_walkable(*t["point"]) for t in target_list]
    nodes = [start_rc, *target_rcs]

    dist, paths = _pairwise_paths(grid, nodes)
    order, length_m = _best_order(dist, len(target_list))

    route_indices = [0, *order, 0]
    polyline_xy: list[list[float]] = []
    for k in range(len(route_indices) - 1):
        seg = paths[(route_indices[k], route_indices[k + 1])]
        if seg is None:
            continue
        seg_xy = [list(grid.to_xy(r, c)) for r, c in seg]
        if polyline_xy and polyline_xy[-1] == seg_xy[0]:
            seg_xy = seg_xy[1:]
        polyline_xy.extend(seg_xy)

    separate_length = 2 * sum(dist[0][i] for i in range(1, len(nodes)))
    saved = separate_length - length_m
    reduction_pct = (saved / separate_length * 100) if separate_length else 0.0

    ordered_targets = [target_list[i - 1] for i in order]

    return {
        "targets": target_list,
        "route": {
            "order": [t["id"] for t in ordered_targets],
            "length_m": round(length_m, 1),
            "polyline_local": polyline_xy,
            "polyline_world": [list(real_site.to_world(x, y)) for x, y in polyline_xy],
            "comparison": {
                "separate_trips_m": round(separate_length, 1),
                "optimized_tour_m": round(length_m, 1),
                "saved_m": round(saved, 1),
                "reduction_pct": round(reduction_pct, 1),
            },
        },
        "message": None,
    }
