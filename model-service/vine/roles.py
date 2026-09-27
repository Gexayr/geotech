"""Two role-specific route planners, each on its own graph of the vineyard.

FARMER  - field work (spraying, mowing, pruning, harvest): every inter-row must be worked once
          (lanes="all"), or just enough inter-rows that every row is reached from one side
          (lanes="cover", e.g. a sprayer treating both neighbouring rows).
          Graph: nodes = the two ends of every inter-row lane (+ start); edges = lanes (work),
          headland turns between lane ends on the same side of a block, and transfers between
          blocks (shortest walkable paths). This is a Rural Postman problem, solved as a
          generalised TSP over oriented lanes (each lane appears twice, A->B and B->A, exactly one
          of them is used) with OR-Tools guided local search from a nearest-neighbour start.
          Turn cost follows the vehicle: on foot a turn costs its length; with a turning radius R
          a turn into a lane closer than 2R needs a bulb/fishtail manoeuvre, so the solver picks
          skip-row patterns for tractors by itself.
INSPECTOR - scouting: visits only the problem spots (row gaps, waste), priority weighted
          (waste 3, gaps 1 + gap/10 m, capped), optional time budget, legal ground only
          (<= 1.9 % off authorised passages / inter-rows). Graph: nodes = stops, edges = shortest
          legal paths. Prize-collecting TSP (orienteering), see route_opt.
"""
import math
import time

import geopandas as gpd
import numpy as np
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from .common import work_crs
from .instructions import _bearing, _compass
from .lawnmower import lane_of, select_lanes
from .route import Grid, coverage, RES

TURN_OUT_M = 1.5            # headland clearance past the row ends
BIG_M = 1e6                 # "unreachable" distance
FARMER_SOLVE_S = 40
MAX_CELLS = 1.2e6            # farmer transfer grid size limit (cells)


# ------------------------------------------------------------------ helpers
def turn_length(d, R):
    """Headland turn between two parallel lanes d metres apart for turning radius R (0 = on foot).
    d >= 2R: two quarter circles + straight = pi R + d - 2R;  d < 2R: bulb / fishtail manoeuvre,
    approximated as pi R + 3 (2R - d) (reversing is slow, so it is penalised)."""
    d = np.asarray(d, float)
    if R <= 0:
        return d
    return np.where(d >= 2 * R, math.pi * R + d - 2 * R, math.pi * R + 3 * (2 * R - d))


def open_ground(layers, passages, forbidden, ti, start):
    """Farmers may use all open ground of the farm (headlands, tracks) except forbidden zones."""
    area = coverage(ti, layers, passages, start)
    bl = layers.get("blocks")
    geoms = list(bl.geometry) if bl is not None and len(bl) else list(layers["interrow_area"].geometry.buffer(1.5))
    g = area.difference(unary_union(geoms).buffer(0.3))
    if passages is not None:
        g = g.union(passages)
    if forbidden is not None:
        g = g.difference(forbidden)
    return g


def _lanes(layers, mode):
    inter, rows = layers["interrow_area"], layers["row"]
    blocks = []
    if not len(inter):
        return blocks
    for vid in sorted(set(inter["vineyard_id"])):
        polys = list(inter[inter["vineyard_id"] == vid].geometry)
        lanes = [ln for ln in (lane_of(p) for p in polys) if ln is not None]
        if not lanes:
            continue
        if mode == "cover" and len(rows):
            sel = select_lanes(lanes, list(rows[rows["vineyard_id"] == vid].geometry))
            lanes = sel[0] if isinstance(sel, tuple) and sel[0] else lanes
        c = np.asarray(lanes[0].coords)
        d = (c[-1] - c[0]) / max(np.hypot(*(c[-1] - c[0])), 1e-9)
        nrm = np.array([-d[1], d[0]])
        L = []
        for ln in lanes:
            q = np.asarray(ln.coords)
            if np.dot(q[-1] - q[0], d) < 0:
                q = q[::-1]
            L.append(dict(a=q[0], b=q[-1], line=LineString(q), length=float(np.hypot(*(q[-1] - q[0]))),
                          off=float(np.dot((q[0] + q[-1]) / 2, nrm))))
        L.sort(key=lambda z: z["off"])
        offs = np.array([z["off"] for z in L])
        spacing = float(np.median(np.diff(offs))) if len(L) > 1 else 2.5
        for k, z in enumerate(L, 1):
            z["id"] = f"{vid}-L{k:02d}"
        blocks.append(dict(vid=vid, d=d, nrm=nrm, lanes=L, spacing=spacing))
    return blocks


# ------------------------------------------------------------------ farmer
def plan_farmer(layers, start, passages, forbidden, ti, log=print, lanes="all", turn_radius=0.0,
                speed_kmh=4.0, solve_s=FARMER_SOLVE_S):
    t0 = time.time()
    blocks = _lanes(layers, lanes)
    if not blocks:
        raise RuntimeError("no inter-row lanes to work")
    farm = open_ground(layers, passages, forbidden, ti, start)
    L_, B_, R_, T_ = coverage(ti, layers, farm, start).bounds
    res = max(RES, float(np.sqrt((R_ - L_) * (T_ - B_) / MAX_CELLS)))    # transfers only: coarse grid on big farms
    grid = Grid(layers, farm, forbidden, ti, start, log, res=res)

    # ---- oriented lane nodes: 2 per lane (o=0: A->B, o=1: B->A); node 0 = start
    N = [None]
    for bi, b in enumerate(blocks):
        for li, z in enumerate(b["lanes"]):
            for o in (0, 1):
                ent, ext = (z["a"], z["b"]) if o == 0 else (z["b"], z["a"])
                N.append(dict(b=bi, l=li, o=o, ent=ent, ext=ext, ens=o, exs=1 - o, len=z["length"],
                              off=z["off"], id=z["id"]))
    n = len(N)
    nl = (n - 1) // 2
    log(f"farmer: {len(blocks)} blocks, {nl} inter-row lanes ({lanes}), turning radius {turn_radius:g} m; graph {n} nodes")
    B = np.array([-1] + [v["b"] for v in N[1:]])
    ENS = np.array([-1] + [v["ens"] for v in N[1:]])
    EXS = np.array([-1] + [v["exs"] for v in N[1:]])
    OFF = np.array([0.0] + [v["off"] for v in N[1:]])
    LEN = np.array([0.0] + [v["len"] for v in N[1:]])
    D_ = [np.array([1.0, 0.0])] + [blocks[v["b"]]["d"] for v in N[1:]]
    PE = np.array([(0.0, 0.0)] + [tuple(v["ent"]) for v in N[1:]])
    PX = np.array([(0.0, 0.0)] + [tuple(v["ext"]) for v in N[1:]])
    DV = np.array(D_)
    PE[0] = PX[0] = (start.x, start.y)

    # ---- corner ports (first / last lane of each block side) -> shortest paths between them
    corners = []                                            # (block, side, xy)
    for bi, b in enumerate(blocks):
        for side in (0, 1):
            for z in (b["lanes"][0], b["lanes"][-1]):
                corners.append((bi, side, z["a"] if side == 0 else z["b"]))
    src_nodes = [grid.node(start.x, start.y, passable=False)] + [grid.node(*c[2]) for c in corners]
    uniq = sorted(set(src_nodes))
    log(f"farmer: shortest paths between {len(uniq)} block corners ...")
    Du = np.zeros((len(uniq), len(uniq)))
    for s0 in range(0, len(uniq), 8):
        Du[s0:s0 + 8] = dijkstra(grid.G, directed=False, indices=uniq[s0:s0 + 8])[:, uniq]
    Du = np.minimum(Du, Du.T)
    pos = {u: i for i, u in enumerate(uniq)}
    ci = np.array([pos[u] for u in src_nodes])
    Dc = Du[np.ix_(ci, ci)]                                 # index 0 = start, 1.. = corners
    reach = np.isfinite(Dc[0])
    Dc = np.where(np.isfinite(Dc), Dc, BIG_M)
    dead = {bi for bi in range(len(blocks)) if not any(reach[1 + k] for k, c in enumerate(corners) if c[0] == bi)}
    if dead:
        log(f"farmer: {len(dead)} block(s) unreachable from the start - skipped")

    # per node: the 2 corners on its entry / exit side, with headland distance to them
    cmap = {}
    for k, (bi, side, xy) in enumerate(corners):
        cmap.setdefault((bi, side), []).append((k + 1, np.asarray(xy)))

    def hd(pt, bi, xy):
        d, nrm = blocks[bi]["d"], blocks[bi]["nrm"]
        v = np.asarray(pt) - xy
        return abs(np.dot(v, d)) + abs(np.dot(v, nrm))

    CX, HX, CE, HE = (np.zeros((n, 2), int), np.zeros((n, 2)), np.zeros((n, 2), int), np.zeros((n, 2)))
    for u in range(1, n):
        v = N[u]
        for k, (cid, xy) in enumerate(cmap[(v["b"], v["exs"])]):
            CX[u, k], HX[u, k] = cid, hd(v["ext"], v["b"], xy)
        for k, (cid, xy) in enumerate(cmap[(v["b"], v["ens"])]):
            CE[u, k], HE[u, k] = cid, hd(v["ent"], v["b"], xy)
    # transfer distance exit(u) -> entry(v) through the corner network
    T = np.full((n, n), np.inf)
    for k in range(2):
        for l_ in range(2):
            T = np.minimum(T, HX[:, k][:, None] + Dc[np.ix_(CX[:, k], CE[:, l_])] + HE[:, l_][None, :])
    T[0, :] = np.min([Dc[0, CE[:, l_]] + HE[:, l_] for l_ in range(2)], axis=0)
    T[:, 0] = np.min([HX[:, k] + Dc[CX[:, k], 0] for k in range(2)], axis=0)
    # headland turn exit(u) -> entry(v), same block and same side
    same = (B[:, None] == B[None, :]) & (EXS[:, None] == ENS[None, :]) & (B[:, None] >= 0)
    lat = np.abs(OFF[:, None] - OFF[None, :])
    along = np.abs(np.einsum("ik,ik->i", PX, DV)[:, None] - np.einsum("jk,ik->ij", PE, DV))
    H = 2 * TURN_OUT_M + turn_length(lat, turn_radius) + along
    C = np.where(same, H, T)                               # same side: the headland turn is the real path
    C = C + LEN[None, :]                                    # entering a lane = working it
    lane_of_node = np.array([-1] + [(u - 1) // 2 for u in range(1, n)])
    C[lane_of_node[:, None] == lane_of_node[None, :]] = BIG_M
    np.fill_diagonal(C, 0)
    Ci = np.round(np.minimum(C, BIG_M) * 10).astype(np.int64)

    # ---- nearest-neighbour start, then OR-Tools GLS
    active = [u for u in range(1, n) if N[u]["b"] not in dead]
    todo = {lane_of_node[u] for u in active}
    cur, tour, used = 0, [], set()
    while len(used) < len(todo):
        cand = [u for u in active if lane_of_node[u] not in used]
        u = cand[int(np.argmin(Ci[cur, cand]))]
        tour.append(u)
        used.add(lane_of_node[u])
        cur = u
    nn_cost = Ci[0, tour[0]] + sum(Ci[a, b] for a, b in zip(tour[:-1], tour[1:])) + Ci[tour[-1], 0]
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
        man = pywrapcp.RoutingIndexManager(n, 1, 0)
        rt = pywrapcp.RoutingModel(man)
        cb = rt.RegisterTransitMatrix(Ci.tolist())
        rt.SetArcCostEvaluatorOfAllVehicles(cb)
        pen = int(Ci[Ci < BIG_M * 10].max() * 4 + 10 ** 7)
        for li in range(nl):
            if N[1 + 2 * li]["b"] in dead:
                rt.AddDisjunction([man.NodeToIndex(1 + 2 * li), man.NodeToIndex(2 + 2 * li)], 0, 1)
            else:
                rt.AddDisjunction([man.NodeToIndex(1 + 2 * li), man.NodeToIndex(2 + 2 * li)], pen, 1)
        prm = pywrapcp.DefaultRoutingSearchParameters()
        prm.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        t_lim = min(solve_s, 3.0 + 0.04 * n)
        prm.time_limit.FromMilliseconds(int(t_lim * 1000))
        rt.CloseModelWithParameters(prm)
        init = rt.ReadAssignmentFromRoutes([tour], True)
        sol = rt.SolveFromAssignmentWithParameters(init, prm) if init is not None else rt.SolveWithParameters(prm)
        if sol is not None:
            i, t2 = rt.Start(0), []
            i = sol.Value(rt.NextVar(i))
            while not rt.IsEnd(i):
                t2.append(man.IndexToNode(i))
                i = sol.Value(rt.NextVar(i))
            c2 = Ci[0, t2[0]] + sum(Ci[a, b] for a, b in zip(t2[:-1], t2[1:])) + Ci[t2[-1], 0]
            if len({lane_of_node[u] for u in t2}) == len(todo) and c2 <= nn_cost:
                log(f"farmer: nearest-neighbour {nn_cost / 10:.0f} m -> OR-Tools GLS {c2 / 10:.0f} m ({t_lim:.0f} s)")
                tour = t2
    except ImportError:                                    # pragma: no cover
        log("farmer: OR-Tools not installed - using the nearest-neighbour order")

    # ---- geometry + instructions
    coords, steps = [(start.x, start.y)], []
    work_m = turn_m = transfer_m = 0.0
    n_skip = 0
    prev = 0

    def grid_path(p, q):
        a_, b_ = grid.node(*p, passable=False), grid.node(*q, passable=False)
        pth = grid.leg(a_, b_, limit=max(200.0, 3 * np.hypot(p[0] - q[0], p[1] - q[1]) + 100))
        return [tuple(p)] + ([grid.xy(m) for m in pth] if pth else []) + [tuple(q)]

    for u in tour + [0]:
        a_pt = PX[prev] if prev else np.array([start.x, start.y])
        b_pt = PE[u] if u else np.array([start.x, start.y])
        is_turn = bool(prev and u and same[prev, u])
        if is_turn:
            d = DV[prev] * (1 if N[prev]["o"] == 0 else -1)          # heading when leaving the lane
            m = max(np.dot(a_pt, d), np.dot(b_pt, d)) + TURN_OUT_M
            seg = [tuple(a_pt), tuple(a_pt + d * (m - np.dot(a_pt, d))), tuple(b_pt + d * (m - np.dot(b_pt, d))),
                   tuple(b_pt)]
            L_ = LineString(seg).length
            turn_m += L_
            lat_v = np.asarray(b_pt) - np.asarray(a_pt)
            side = "left" if d[0] * lat_v[1] - d[1] * lat_v[0] > 0 else "right"
            k = int(round(abs(OFF[u] - OFF[prev]) / max(blocks[N[u]["b"]]["spacing"], 0.5))) - 1
            n_skip += k > 0
            what = "the next inter-row" if k <= 0 else f"inter-row {N[u]['id']} (skip {k})"
            steps.append(dict(kind="turn", length_m=round(L_, 1), coords=seg,
                              text=f"At the headland turn {side} into {what}."))
        else:
            seg = grid_path(a_pt, b_pt)
            L_ = LineString(seg).length if len(seg) > 1 else 0.0
            transfer_m += L_
            if u:
                steps.append(dict(kind="transfer", length_m=round(L_, 1), coords=seg,
                                  text=(f"Go {L_:.0f} m around the block end to inter-row {N[u]['id']}."
                                        if prev and N[prev]["b"] == N[u]["b"] else
                                        f"Go {L_:.0f} m to block {blocks[N[u]['b']]['vid']}, start of inter-row {N[u]['id']}.")))
            else:
                steps.append(dict(kind="return", length_m=round(L_, 1), coords=seg,
                                  text=f"Work finished - return {L_:.0f} m to the start."))
        coords += seg[1:]
        if u:
            ln = [tuple(PE[u]), tuple(PX[u])]
            coords += ln[1:]
            work_m += N[u]["len"]
            steps.append(dict(kind="work", length_m=round(N[u]["len"], 1), coords=ln, lane=N[u]["id"],
                              text=f"Work inter-row {N[u]['id']}: {N[u]['len']:.0f} m heading "
                                   f"{_compass(_bearing(ln[0], ln[1]))}."))
        prev = u
    line = LineString(coords)
    tot = line.length
    hrs = tot / 1000 / max(speed_kmh, 0.1)
    props = dict(role="farmer", length_m=round(tot, 1), work_m=round(work_m, 1), turn_m=round(turn_m, 1),
                 transfer_m=round(transfer_m, 1), efficiency_pct=round(100 * work_m / max(tot, 1e-9), 1),
                 lanes_worked=len(tour), lanes_total=nl, blocks=len(blocks) - len(dead), skip_turns=n_skip,
                 turn_radius_m=turn_radius, lane_mode=lanes, speed_kmh=speed_kmh, minutes=round(hrs * 60, 1),
                 lane_order=",".join(N[u]["id"] + ("" if N[u]["o"] == 0 else "r") for u in tour),
                 solver="rural postman as generalised TSP over oriented lanes, NN + OR-Tools GLS")
    log(f"farmer route {props['length_m']} m ({props['minutes']} min at {speed_kmh:g} km/h): work {props['work_m']} m, "
        f"turns {props['turn_m']} m, transfers {props['transfer_m']} m, efficiency {props['efficiency_pct']} %, "
        f"{time.time() - t0:.0f} s")
    g = gpd.GeoDataFrame([dict(props, geometry=line)], geometry="geometry", crs=work_crs())
    g.attrs["start"] = (start.x, start.y)
    g.attrs["steps"] = steps
    # graph: lane-end nodes, work edges, headland edges between neighbouring lanes, used transfers
    feats = [dict(element="node", id="START", kind="start", geometry=Point(start.x, start.y))]
    for b in blocks:
        for z in b["lanes"]:
            feats += [dict(element="node", id=z["id"] + "-A", kind="lane_end", geometry=Point(*z["a"])),
                      dict(element="node", id=z["id"] + "-B", kind="lane_end", geometry=Point(*z["b"])),
                      dict(element="edge", id=z["id"], kind="work", length_m=round(z["length"], 1),
                           frm=z["id"] + "-A", to=z["id"] + "-B", geometry=z["line"])]
        for z0, z1 in zip(b["lanes"][:-1], b["lanes"][1:]):
            for s_ in "AB":
                p0, p1 = (z0["a"], z1["a"]) if s_ == "A" else (z0["b"], z1["b"])
                feats.append(dict(element="edge", id=f"{z0['id']}-{s_}~{z1['id']}-{s_}", kind="headland",
                                  length_m=round(float(np.hypot(*(p1 - p0))), 1), frm=z0["id"] + "-" + s_,
                                  to=z1["id"] + "-" + s_, geometry=LineString([p0, p1])))
    for k, s_ in enumerate(steps):
        if s_["kind"] in ("transfer", "return") and len(s_["coords"]) > 1:
            feats.append(dict(element="edge", id=f"T{k}", kind=s_["kind"], length_m=s_["length_m"], frm="", to="",
                              geometry=LineString(s_["coords"])))
    g.attrs["graph"] = gpd.GeoDataFrame(feats, geometry="geometry", crs=work_crs())
    return g


# ------------------------------------------------------------------ inspector
def inspector_weight(kind, rec):
    if kind == "waste":
        return 3.0
    gap = rec.get("gap_m") or 0.0
    try:
        gap = float(gap)
    except (TypeError, ValueError):
        gap = 0.0
    return 1.0 + min(gap, 30.0) / 10.0


def _check_text(kind, p):
    if kind == "waste":
        return f"Waste object ({p.get('kind', 'unknown')}): identify, photograph, remove or report."
    gap = p.get("gap_m")
    return (f"Row {p.get('row_id', '?')}: {float(gap):.1f} m planting gap - count missing vines, check for dead stock, "
            f"mark for replanting." if gap not in (None, "") else "Row gap: check missing vines.")


def plan_inspector(layers, start, passages, forbidden, ti, log=print, solve_s=45, max_minutes=None, speed_kmh=4.0):
    from .route_opt import plan_route_optimal
    budget = max_minutes / 60 * speed_kmh * 1000 if max_minutes else None
    r = plan_route_optimal(layers, start, passages, forbidden, ti, log, solve_s=solve_s,
                           weight=inspector_weight, max_length_m=budget)
    r["role"] = "inspector"
    r["minutes"] = round(float(r["length_m"].iloc[0]) / 1000 / speed_kmh * 60, 1)
    info = {}
    for lay, idc in (("inspection", "inspection_id"), ("waste", "waste_id")):
        g = layers.get(lay)
        if g is not None and len(g):
            for rec in g.to_dict("records"):
                info[rec[idc]] = (lay, rec)
    rows, cum, stop = [], 0.0, 0
    feats = [dict(element="node", id="START", kind="start", geometry=Point(start.x, start.y))]
    for leg in r.attrs["legs"]:
        cum += LineString(leg["coords"]).length if len(leg["coords"]) > 1 else 0.0
        if len(leg["coords"]) > 1:
            feats.append(dict(element="edge", id=f"{leg['frm']}->{leg['to']}", kind="leg",
                              length_m=round(LineString(leg["coords"]).length, 1), frm=leg["frm"], to=leg["to"],
                              geometry=LineString(leg["coords"])))
        if leg["to"] == "START":
            continue
        stop += 1
        for tid in leg["covers"]:
            kind, p = info.get(tid, ("inspection", {}))
            c = p["geometry"].centroid if "geometry" in p else Point(leg["coords"][-1])
            rows.append(dict(order=len(rows) + 1, stop=stop, id=tid, kind=kind, vineyard_id=p.get("vineyard_id", ""),
                             row_id=p.get("row_id", ""), gap_m=p.get("gap_m", ""), priority=round(inspector_weight(kind, p), 2),
                             what_to_check=_check_text(kind, p), cumulative_m=round(cum, 1), x=round(c.x, 2),
                             y=round(c.y, 2), geometry=c))
            feats.append(dict(element="node", id=tid, kind=kind, geometry=c))
    chk = gpd.GeoDataFrame(rows, geometry="geometry", crs=work_crs()) if rows else \
        gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=work_crs())
    r.attrs["checklist"] = chk
    r.attrs["graph"] = gpd.GeoDataFrame(feats, geometry="geometry", crs=work_crs())
    return r


# ------------------------------------------------------------------ both, written to disk
def write_roles(out, layers, start, passages, forbidden, ti, log=print, roles=("farmer", "inspector"),
                farmer_lanes="all", turn_radius=0.0, farmer_speed=4.0, max_minutes=None, solve_s=45, crs=None):
    """Plan the requested role routes and write route_<role>.geojson, graph_<role>.geojson,
    instructions_<role>.txt, inspector_checklist.csv (+ wgs84 copies). Returns {role: GeoDataFrame}."""
    import os
    from .instructions import Context, build_instructions
    os.makedirs(os.path.join(out, "wgs84"), exist_ok=True)
    res = {}

    def save(g, name):
        if crs is not None:
            g = g.set_crs(crs, allow_override=True)
        g.to_file(os.path.join(out, name + ".geojson"), driver="GeoJSON")
        if g.crs is not None and not g.crs.is_geographic and len(g):
            g.to_crs("EPSG:4326").to_file(os.path.join(out, "wgs84", name + ".geojson"), driver="GeoJSON")

    if "farmer" in roles:
        f = plan_farmer(layers, start, passages, forbidden, ti, log, lanes=farmer_lanes, turn_radius=turn_radius,
                        speed_kmh=farmer_speed, solve_s=solve_s)
        save(f, "route_farmer")
        save(f.attrs["graph"], "graph_farmer")
        with open(os.path.join(out, "instructions_farmer.txt"), "w", encoding="utf-8") as fh:
            p = f.iloc[0]
            fh.write(f"FARMER ROUTE - {p['length_m']} m, about {p['minutes']} min at {p['speed_kmh']} km/h; "
                     f"{p['lanes_worked']} inter-rows, work {p['work_m']} m ({p['efficiency_pct']} % of the route)\n\n")
            for i, s_ in enumerate(f.attrs["steps"], 1):
                fh.write(f"{i:4d}. {s_['text']}\n")
        res["farmer"] = f
    if "inspector" in roles:
        r = plan_inspector(layers, start, passages, forbidden, ti, log, solve_s=solve_s, max_minutes=max_minutes)
        save(r, "route_inspector")
        save(r.attrs["graph"], "graph_inspector")
        chk = r.attrs["checklist"]
        if len(chk):
            c2 = chk.set_crs(crs, allow_override=True) if crs is not None else chk
            if c2.crs is not None and not c2.crs.is_geographic:
                ll = c2.to_crs("EPSG:4326").geometry
                chk = chk.assign(lon=ll.x.round(7), lat=ll.y.round(7))
        chk.drop(columns="geometry").to_csv(os.path.join(out, "inspector_checklist.csv"), index=False)
        tg = {}
        for lay, idc in (("inspection", "inspection_id"), ("waste", "waste_id")):
            g = layers.get(lay)
            for rec in g.to_dict("records") if g is not None and len(g) and idc in g else []:
                tg[rec[idc]] = dict(kind=lay, props=rec, id=rec[idc])
        ins = build_instructions(r.attrs["legs"], tg, Context(layers["interrow_area"], passages))
        r.attrs["instructions"] = ins
        with open(os.path.join(out, "instructions_inspector.txt"), "w", encoding="utf-8") as fh:
            p = r.iloc[0]
            fh.write(f"INSPECTOR ROUTE - {p['length_m']} m, about {p['minutes']} min; {p['n_targets']} problem spots\n\n")
            for leg in ins:
                fh.write(f"Leg {leg['leg']}: {leg['frm']} -> {leg['to']} ({leg['length_m']} m, total {leg['cumulative_m']} m)\n")
                for i, s_ in enumerate(leg["steps"], 1):
                    fh.write(f"   {i}. {s_['text']}\n")
                fh.write(f"   -> {leg['arrive']}\n\n")
        res["inspector"] = r
    _write_json(os.path.join(out, "roles.json"), res, crs)
    return res


def _write_json(path, res, crs):
    """roles.json: both routes with steps / checklist in lon,lat (API payload for web and mobile clients)."""
    import json
    tf = None
    if crs is not None:
        from pyproj import CRS as PCRS, Transformer
        if not PCRS.from_user_input(crs).is_geographic:
            tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    ll = (lambda cs: [[round(v, 7) for v in tf.transform(float(p[0]), float(p[1]))] for p in cs]) if tf else \
        (lambda cs: [[round(float(v), 2) for v in p] for p in cs])
    py = lambda d: {k: (v.item() if hasattr(v, "item") else v) for k, v in d.items() if k != "geometry"}  # noqa: E731
    out = dict(crs="EPSG:4326" if tf else str(crs))
    if "farmer" in res:
        f = res["farmer"]
        out["farmer"] = dict(summary=py(f.iloc[0].to_dict()),
                             steps=[dict(kind=s["kind"], text=s["text"], length_m=s["length_m"], coords=ll(s["coords"]))
                                    for s in f.attrs["steps"]])
    if "inspector" in res:
        r = res["inspector"]
        out["inspector"] = dict(
            summary={k: v for k, v in py(r.iloc[0].to_dict()).items() if k not in ("skipped", "unreachable")},
            checklist=[py(c) for c in r.attrs["checklist"].to_dict("records")],
            instructions=[dict({k: v for k, v in leg.items() if k != "steps"},
                               steps=[dict(text=s["text"], length_m=s["length_m"], coords=ll(s["coords"]))
                                      for s in leg["steps"]]) for leg in r.attrs.get("instructions", [])])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
