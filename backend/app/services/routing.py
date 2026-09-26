"""Route optimization over the organizer-defined walking network.

Builds a weighted graph from the passable-path nodes/edges (headlands +
inter-row passages), then finds the shortest closed tour from the start
point that visits every target and returns. With only a handful of stops
an exact brute-force over permutations is fine; swap for OR-Tools/Held-Karp
if the target count grows.
"""

import itertools
import math

import networkx as nx

from app.data import demo_v001 as demo
from app.schemas.geo import RouteComparison, RouteResponse


def _euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.dist(a, b)


def _build_graph() -> nx.Graph:
    g = nx.Graph()
    for node_id, coords in demo.NODES.items():
        g.add_node(node_id, pos=coords)
    for a, b in demo.EDGES:
        g.add_edge(a, b, weight=_euclidean(demo.NODES[a], demo.NODES[b]))
    return g


def _stop_ids() -> list[str]:
    return [t["target_id"] for t in demo.TARGETS] + [w["waste_id"] for w in demo.WASTE]


def _best_order(graph: nx.Graph, start: str, stops: list[str]) -> tuple[list[str], float]:
    best_order: list[str] | None = None
    best_length = math.inf
    for perm in itertools.permutations(stops):
        route = [start, *perm, start]
        length = sum(
            nx.shortest_path_length(graph, route[i], route[i + 1], weight="weight")
            for i in range(len(route) - 1)
        )
        if length < best_length:
            best_length = length
            best_order = list(perm)
    return best_order or [], best_length


def _separate_trips_length(graph: nx.Graph, start: str, stops: list[str]) -> float:
    return 2 * sum(
        nx.shortest_path_length(graph, start, stop, weight="weight") for stop in stops
    )


def _polyline_for_order(graph: nx.Graph, start: str, order: list[str]) -> list[list[float]]:
    route = [start, *order, start]
    coords: list[list[float]] = []
    for i in range(len(route) - 1):
        path = nx.shortest_path(graph, route[i], route[i + 1], weight="weight")
        segment = [list(demo.NODES[node]) for node in path]
        if coords and coords[-1] == segment[0]:
            segment = segment[1:]
        coords.extend(segment)
    return coords


def compute_route(block_id: str) -> RouteResponse:
    graph = _build_graph()
    stops = _stop_ids()
    order, optimized_length = _best_order(graph, demo.START_ID, stops)
    separate_length = _separate_trips_length(graph, demo.START_ID, stops)
    polyline = _polyline_for_order(graph, demo.START_ID, order)

    saved = separate_length - optimized_length
    reduction_pct = (saved / separate_length * 100) if separate_length else 0.0

    return RouteResponse(
        block_id=block_id,
        start_id=demo.START_ID,
        start_point=list(demo.START_POINT),
        order=order,
        targets_visited=len(order),
        targets_total=len(stops),
        length_m=round(optimized_length, 1),
        polyline=polyline,
        comparison=RouteComparison(
            separate_trips_m=round(separate_length, 1),
            optimized_tour_m=round(optimized_length, 1),
            saved_m=round(saved, 1),
            reduction_pct=round(reduction_pct, 1),
        ),
    )
