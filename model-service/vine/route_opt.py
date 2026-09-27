"""Optimal walking route under the scoring rules: prize-collecting TSP with a hard off-passable budget.

    maximise   number of targets visited (a target counts when the route passes within 2 m)
    then       minimise route length
    subject to closed tour from the organiser start point,
               off-passable length <= MAX_OUT_FRAC x route length   (scoring voids routes above 2 %)

Model: every target (row-gap inspection point, waste) is an optional node with a large prize
(OR-Tools disjunction penalty); arc cost = true shortest-path length; an "outside" dimension
accumulates the off-passable metres of each arc with capacity = budget. Because the budget is a
fraction of the (unknown) final length, the model is re-solved until budget and length agree.
Arc lengths and off-passable metres are measured exactly along each shortest path (pointer doubling
over the Dijkstra predecessor tree). Solved with guided local search (NP-hard; no exact optimum at
this size, the solver returns the best tour found in the time limit).
"""
import time

import geopandas as gpd
import numpy as np
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString

from .common import work_crs
from .route import Grid, _load_start, RES, VISIT_M, MERGE_M

MAX_OUT_FRAC = 0.019
PRIZE = 10 ** 7            # >> any route length (dm): visiting one more target always beats being shorter
SOLVE_S = 45
ITER = 4


def _tree_sums(pred, src, fx, fy, goodv):
    """Length and off-passable length of the shortest path from src to every node (pointer doubling)."""
    n = len(pred)
    p = pred.astype(np.int64).copy()
    p[p < 0] = np.arange(n)[p < 0]                      # roots / unreachable point to themselves
    p[src] = src
    step = np.hypot(fx - fx[p], fy - fy[p]) * RES
    bad = ~(goodv & goodv[p])
    L = step.copy()
    O = np.where(bad, step, 0.0)
    for _ in range(25):
        L = L + L[p] * (p != np.arange(n))
        O = O + O[p] * (p != np.arange(n))
        p2 = p[p]
        if np.array_equal(p2, p):
            break
        p = p2
    return L, O                                         # exact (Wyllie list ranking on the tree)


def plan_route_optimal(layers, start_path, passages, forbidden, ti, log=print, solve_s=SOLVE_S, weight=None,
                       max_length_m=None):
    """weight(kind, record) -> priority of a target (default 1); max_length_m: optional walking budget."""
    t0 = time.time()
    start = start_path if hasattr(start_path, "x") else _load_start(start_path)
    grid = Grid(layers, passages, forbidden, ti, start, log)
    # ---- targets (reachable = passable ground within 2 m), merged when one stop serves several
    targets = []
    for lay, idc in (("inspection", "inspection_id"), ("waste", "waste_id")):
        g = layers.get(lay)
        if g is not None and len(g):
            for r in g.itertuples():
                p = r.geometry.centroid
                rr, cc = grid.rc(p.x, p.y)
                if grid.dg[rr, cc] <= VISIT_M - 0.25:
                    w = float(weight(lay, r._asdict())) if weight else 1.0
                    targets.append(dict(id=getattr(r, idc), kind=lay, node=grid.node(p.x, p.y), w=w))
    stops = []
    for t in targets:
        for s in stops:
            if np.hypot(grid.fx[s["node"]] - grid.fx[t["node"]], grid.fy[s["node"]] - grid.fy[t["node"]]) * RES <= MERGE_M:
                s["ids"].append(t["id"])
                s["w"] += t["w"]
                break
        else:
            stops.append(dict(node=t["node"], ids=[t["id"]], w=t["w"]))
    s_node = grid.node(start.x, start.y, passable=False)
    nodes = [s_node] + [s["node"] for s in stops]
    n = len(nodes)
    log(f"optimal route: {len(targets)} reachable targets in {n - 1} stops; measuring {n}x{n} shortest paths ...")

    # ---- exact length / off-passable metres for every pair (along the cost-optimal path)
    Lm, Om = np.full((n, n), np.inf), np.full((n, n), np.inf)
    for i, s in enumerate(nodes):
        d, pr = dijkstra(grid.G, directed=False, indices=s, return_predecessors=True)
        Lt, Ot = _tree_sums(pr, s, grid.fx, grid.fy, grid.goodv)
        fin = np.isfinite(d[nodes])
        Lm[i, fin] = Lt[np.array(nodes)[fin]]
        Om[i, fin] = Ot[np.array(nodes)[fin]]
        if i % 25 == 0:
            log(f"   {i + 1}/{n} sources")
    reach = np.isfinite(Lm[0])
    keep = [i for i in range(n) if reach[i]]
    log(f"   {n - len(keep)} stop(s) unreachable from the start")
    Lm, Om = Lm[np.ix_(keep, keep)], Om[np.ix_(keep, keep)]
    Lm = np.minimum(Lm, Lm.T)
    Om = np.where(Lm == Lm.T, np.minimum(Om, Om.T), Om)
    stops_k = [stops[i - 1] for i in keep[1:]]
    nodes_k = [nodes[i] for i in keep]
    m = len(nodes_k)

    # ---- OR-Tools prize-collecting routing with an "outside" capacity dimension
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    Li = np.round(Lm * 10).astype(np.int64)
    Oi = np.round(Om * 10).astype(np.int64)

    def solve(budget_m, t_s):
        man = pywrapcp.RoutingIndexManager(m, 1, 0)
        rt = pywrapcp.RoutingModel(man)
        cb = rt.RegisterTransitCallback(lambda a, b: int(Li[man.IndexToNode(a), man.IndexToNode(b)]))
        rt.SetArcCostEvaluatorOfAllVehicles(cb)
        ocb = rt.RegisterTransitCallback(lambda a, b: int(Oi[man.IndexToNode(a), man.IndexToNode(b)]))
        rt.AddDimension(ocb, 0, int(budget_m * 10), True, "outside")
        if max_length_m:
            rt.AddDimension(cb, 0, int(max_length_m * 10), True, "length")
        for k in range(1, m):
            rt.AddDisjunction([man.NodeToIndex(k)], int(PRIZE * stops_k[k - 1]["w"]))
        prm = pywrapcp.DefaultRoutingSearchParameters()
        prm.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        prm.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        prm.time_limit.FromMilliseconds(int(t_s * 1000))
        sol = rt.SolveWithParameters(prm)
        if sol is None:
            return None
        i, tour = rt.Start(0), []
        while not rt.IsEnd(i):
            tour.append(man.IndexToNode(i))
            i = sol.Value(rt.NextVar(i))
        return tour + [0]

    budget = 400.0
    best = None
    solve_s = min(solve_s, 2.0 + 0.25 * m)                 # small problems do not need the full time limit
    for it in range(ITER):
        tour = solve(budget, solve_s)
        if tour is None:
            budget *= 0.8
            continue
        Ltot = sum(Lm[a, b] for a, b in zip(tour[:-1], tour[1:]))
        Otot = sum(Om[a, b] for a, b in zip(tour[:-1], tour[1:]))
        ntg = sum(len(stops_k[k - 1]["ids"]) for k in tour[1:-1])
        score = sum(stops_k[k - 1]["w"] for k in tour[1:-1])
        log(f"   budget {budget:.0f} m -> {ntg} targets, {Ltot:.0f} m, off-passable {Otot:.0f} m "
            f"({100 * Otot / max(Ltot, 1):.2f} %)")
        feasible = Otot <= MAX_OUT_FRAC * Ltot + 1e-6
        if feasible and (best is None or score > best[0] + 1e-9 or (abs(score - best[0]) <= 1e-9 and Ltot < best[1])):
            best = (score, Ltot, tour)
        nb = MAX_OUT_FRAC * Ltot * (1.0 if feasible else 0.9)
        if feasible and Otot <= nb and ntg == sum(len(s["ids"]) for s in stops_k):
            break                                            # every reachable target already visited legally
        budget = nb
    if best is None:
        raise RuntimeError("no feasible route found")
    score, Ltot, tour = best

    # ---- geometry
    coords = [(start.x, start.y)]
    order_ids, legs = [], []
    for a, b in zip(tour[:-1], tour[1:]):
        p = grid.leg(nodes_k[a], nodes_k[b])
        seg = [grid.xy(q) for q in p] if p else []
        coords += seg
        if b:
            order_ids += stops_k[b - 1]["ids"]
        legs.append(dict(frm=stops_k[a - 1]["ids"][0] if a else "START",
                         to=stops_k[b - 1]["ids"][0] if b else "START",
                         covers=stops_k[b - 1]["ids"] if b else [], coords=seg))
    coords.append((start.x, start.y))
    line = LineString(coords).simplify(0.2)
    outside = line.difference(grid.passable_geom.buffer(0.05)).length
    visited = set(order_ids)
    props = dict(length_m=round(line.length, 1), walk_minutes_4kmh=round(line.length / 4000 * 60, 1),
                 n_targets=len(visited), reachable_targets=len(targets), all_targets=sum(
                     len(layers[k]) for k in ("inspection", "waste") if layers.get(k) is not None),
                 score=round(score, 2),
                 outside_passable_m=round(outside, 1),
                 outside_passable_pct=round(100 * outside / max(line.length, 1e-9), 2),
                 visit_order=",".join(order_ids),
                 skipped=",".join(t["id"] for t in targets if t["id"] not in visited),
                 solver="prize-collecting TSP (orienteering) with hard off-passable budget, OR-Tools GLS",
                 passable_rule="authorised passages + inter-row areas; off-passable <= 1.9 % (hard constraint)",
                 unreachable=",".join(t["id"] for t in targets if t["id"] not in visited),
                 start="organiser start point" if not hasattr(start_path, "x") else "user start point")
    log(f"optimal route: {props['n_targets']}/{len(targets)} reachable targets, {props['length_m']} m "
        f"({props['walk_minutes_4kmh']} min), {props['outside_passable_pct']} % off passable, {time.time() - t0:.0f} s")
    g = gpd.GeoDataFrame([dict(props, geometry=line)], geometry="geometry", crs=work_crs())
    g.attrs["start"] = (start.x, start.y)
    g.attrs["legs"] = legs
    g.attrs["start_dist"] = {i: float(Lm[0, k]) for k in range(1, m) for i in stops_k[k - 1]["ids"]}
    return g
