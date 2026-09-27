"""Lawnmower (boustrophedon) coverage route.

Every vineyard block is swept lane by lane in a serpentine: lanes are inter-row centre lines, and
only as many lanes are walked as needed so that EVERY row axis is within 2 m of the path (a target
counts as visited within 2 m) - with ~2.5-3 m row spacing that is every second inter-row.
Turns between lanes and links between blocks follow shortest paths on the walkability grid
(authorised passages + inter-rows; short connectors only when unavoidable).
Block order, entry corner and direction are optimised (OR-Tools + Viterbi over the 4 sweep variants
of each block); waste and inspection points not covered by the sweeps are added as extra stops.
"""
import time

import geopandas as gpd
import numpy as np
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from .common import work_crs
from .route import Grid, _load_start, solve_tsp_best, RES, VISIT_M

COVER_M = 1.8          # a row is covered when a lane runs within this distance (visit radius 2 m)
LANE_TRIM_M = 0.3
TURN_OUT_M = 1.2       # how far past the lane end the U-turn swings (clears the row ends)


def lane_of(poly):
    """Centre line of a long, narrow inter-row polygon."""
    rr = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    e = [np.hypot(*(rr[(i + 1) % 4] - rr[i])) for i in range(4)]
    i = int(np.argmin(e[:2]))                              # a short side
    a = (rr[i] + rr[(i + 1) % 4]) / 2
    b = (rr[(i + 2) % 4] + rr[(i + 3) % 4]) / 2
    ln = LineString([a, b]).intersection(poly.buffer(0.05))
    parts = [g for g in getattr(ln, "geoms", [ln]) if g.geom_type == "LineString"]
    if not parts:
        return None
    ln = max(parts, key=lambda g: g.length)
    if ln.length < 2 * LANE_TRIM_M + 1:
        return None
    return LineString([ln.interpolate(LANE_TRIM_M), ln.interpolate(ln.length - LANE_TRIM_M)])


def u_turn(prev, nxt):
    """Geometric U-turn at the lane ends: step out past the row ends, cross, step back in."""
    p1, p0 = np.asarray(prev.coords[-1]), np.asarray(prev.coords[-2])
    q0, q1 = np.asarray(nxt.coords[0]), np.asarray(nxt.coords[1])
    d = (p1 - p0) / max(np.hypot(*(p1 - p0)), 1e-9)
    out = TURN_OUT_M + LANE_TRIM_M
    a = p1 + d * out
    e = q0 - (q1 - q0) / max(np.hypot(*(q1 - q0)), 1e-9) * out
    # both outer points on a common line perpendicular to the lanes (the headland)
    m = max(np.dot(a, d), np.dot(e, d))
    a = a + d * (m - np.dot(a, d))
    e = e + d * (m - np.dot(e, d))
    return [tuple(a), tuple(e)]


def select_lanes(lanes, rows):
    """Fewest lanes such that every row is within COVER_M of a chosen lane (interval covering)."""
    if not lanes:
        return []
    d = np.array(np.subtract(*np.asarray(lanes[0].coords)[[1, 0]]))
    d /= np.hypot(*d)
    nrm = np.array([-d[1], d[0]])
    off = lambda g: float(np.dot(np.asarray(g.interpolate(0.5, normalized=True).coords[0]), nrm))  # noqa: E731
    lanes = sorted(lanes, key=off)
    rows = sorted(rows, key=off)
    cov = [[j for j, ln in enumerate(lanes)
            if ln.distance(r) <= COVER_M and r.intersection(ln.buffer(COVER_M)).length > 0.5 * min(r.length, ln.length)]
           for r in rows]
    chosen, covered = [], set()
    for i, c in enumerate(cov):
        if i in covered or not c:
            continue
        j = max(c)                                         # farthest lane that still covers this row
        chosen.append(j)
        covered |= {k for k, cc in enumerate(cov) if j in cc}
    chosen = sorted(set(chosen))
    uncovered = [rows[i] for i, c in enumerate(cov) if not c]
    return [lanes[j] for j in chosen], uncovered


def plan_lawnmower(layers, start_path, passages, forbidden, ti, log=print):
    t0 = time.time()
    start = start_path if hasattr(start_path, "x") else _load_start(start_path)
    sx, sy = start.x, start.y
    grid = Grid(layers, passages, forbidden, ti, start, log)
    inter, rowsg = layers["interrow_area"], layers["row"]

    # ---- per block: lanes, sweep variants (internal path + entry / exit node)
    blocks = []
    for vid in sorted(set(inter["vineyard_id"])):
        polys = inter[inter["vineyard_id"] == vid].geometry
        lanes = [ln for ln in (lane_of(p) for p in polys) if ln is not None]
        rws = list(rowsg[rowsg["vineyard_id"] == vid].geometry)
        if not lanes:
            continue
        chosen, _ = select_lanes(lanes, rws)
        if not chosen:
            continue
        # consistent direction: all lanes pointing the same way
        d0 = np.subtract(*np.asarray(chosen[0].coords)[[1, 0]])
        chosen = [ln if np.dot(np.subtract(*np.asarray(ln.coords)[[1, 0]]), d0) > 0 else
                  LineString(ln.coords[::-1]) for ln in chosen]
        seq = []                                            # serpentine starting at lane 0, forward
        for k, ln in enumerate(chosen):
            seq.append(ln if k % 2 == 0 else LineString(ln.coords[::-1]))
        # internal path for variant A (start lane0 fwd); variant B = start lane0 reversed
        variants = []
        for flip in (False, True):
            s_ = [LineString(ln.coords[::-1]) if flip else ln for ln in seq]
            pts, turn_len, turn_out = [], 0.0, 0.0
            for k, ln in enumerate(s_):
                if k:
                    pts += u_turn(s_[k - 1], ln)
                pts += list(ln.coords)
            variants.append(dict(pts=pts, length=LineString(pts).length, turn_out=turn_out))
        # 4 variants: (A), (A reversed), (B), (B reversed)
        V = []
        for v in variants:
            V.append(dict(pts=v["pts"], length=v["length"], out=v["turn_out"]))
            V.append(dict(pts=v["pts"][::-1], length=v["length"], out=v["turn_out"]))
        for v in V:
            v["entry"], v["exit"] = grid.node(*v["pts"][0]), grid.node(*v["pts"][-1])
        blocks.append(dict(vid=vid, lanes=len(chosen), all_lanes=len(lanes), rows=len(rws), V=V))
        log(f"   block {vid}: {len(chosen)}/{len(lanes)} lanes, sweep {variants[0]['length']:.0f} m")
    log(f"lawnmower: {len(blocks)} blocks, {sum(b['lanes'] for b in blocks)} lanes walked "
        f"(of {sum(b['all_lanes'] for b in blocks)} inter-rows) covering {sum(b['rows'] for b in blocks)} rows")

    # ---- point targets not covered by the sweeps
    sweep_geom = unary_union([LineString(b["V"][0]["pts"]) for b in blocks]) if blocks else None
    pts_t = []
    for lay, idc in (("waste", "waste_id"), ("inspection", "inspection_id")):
        g = layers.get(lay)
        if g is None or not len(g):
            continue
        for r in g.itertuples():
            p = r.geometry.centroid
            if sweep_geom is not None and sweep_geom.distance(p) <= VISIT_M - 0.2:
                continue
            rr, cc = grid.rc(p.x, p.y)
            if grid.dg[rr, cc] > VISIT_M - 0.25:
                continue                                    # no passable ground within 2 m
            pts_t.append(dict(id=getattr(r, idc), node=grid.node(p.x, p.y)))
    log(f"lawnmower: {len(pts_t)} extra point targets (waste / inspection outside the sweeps)")

    # ---- distances between all ports (start, block entries/exits, point targets)
    s_node = grid.node(sx, sy, passable=False)
    ports = [s_node] + [v[e] for b in blocks for v in b["V"] for e in ("entry", "exit")] + [t["node"] for t in pts_t]
    uniq = sorted(set(ports))
    pos = {n: i for i, n in enumerate(uniq)}
    log(f"lawnmower: shortest paths between {len(uniq)} ports ...")
    Dm = np.zeros((len(uniq), len(uniq)))
    for s0 in range(0, len(uniq), 8):
        Dm[s0:s0 + 8] = dijkstra(grid.G, directed=False, indices=uniq[s0:s0 + 8])[:, uniq]
    Dm = np.minimum(Dm, Dm.T)
    reach = np.isfinite(Dm[pos[s_node]])
    # drop block variants / point targets whose ports are unreachable from the start
    for b_ in blocks:
        b_["V"] = [v for v in b_["V"] if reach[pos[v["entry"]]] and reach[pos[v["exit"]]]]
    n_drop = sum(1 for b_ in blocks if not b_["V"])
    blocks = [b_ for b_ in blocks if b_["V"]]
    pts_t = [t for t in pts_t if reach[pos[t["node"]]]]
    if n_drop:
        log(f"lawnmower: {n_drop} block(s) unreachable from the start - skipped")
    Dm = np.where(np.isfinite(Dm), Dm, 1e9)
    dist = lambda a, b: Dm[pos[a], pos[b]]                  # noqa: E731

    # ---- order: OR-Tools on nodes (block = min over its variants), then Viterbi for variants
    nodes = [("S", None)] + [("B", i) for i in range(len(blocks))] + [("P", i) for i in range(len(pts_t))]

    def ends(nd):
        k, i = nd
        if k == "S":
            return [(s_node, s_node, 0.0)]
        if k == "P":
            return [(pts_t[i]["node"], pts_t[i]["node"], 0.0)]
        return [(v["entry"], v["exit"], v["length"]) for v in blocks[i]["V"]]
    n = len(nodes)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                D[i, j] = min(dist(a[1], b[0]) for a in ends(nodes[i]) for b in ends(nodes[j]))
    tour = solve_tsp_best(D)
    seq = [nodes[i] for i in tour]
    # Viterbi: choose one variant per block along the fixed order
    opts = [ends(nd) for nd in seq]
    cost = [np.array([o[2] for o in opts[0]])]
    back = []
    for k in range(1, len(seq)):
        prev = opts[k - 1]
        c = np.array([[cost[-1][pi] + dist(p[1], o[0]) + o[2] for pi, p in enumerate(prev)] for o in opts[k]])
        back.append(c.argmin(1))
        cost.append(c.min(1))
    choice = [int(np.argmin(cost[-1]))]
    for bk in reversed(back):
        choice.append(int(bk[choice[-1]]))
    choice = choice[::-1]

    # ---- build the geometry
    coords, prev_exit, visited_order = [(sx, sy)], s_node, []
    for nd, ch in zip(seq, choice):
        k, i = nd
        if k == "S" and prev_exit == s_node and len(coords) == 1:
            continue
        entry = opts[seq.index(nd)][ch][0] if k != "B" else blocks[i]["V"][ch]["entry"]
        p = grid.leg(prev_exit, entry)
        if p is not None:
            coords += [grid.xy(m) for m in p]
        if k == "B":
            coords += blocks[i]["V"][ch]["pts"]
            prev_exit = blocks[i]["V"][ch]["exit"]
            visited_order.append(blocks[i]["vid"])
        elif k == "P":
            prev_exit = pts_t[i]["node"]
            visited_order.append(pts_t[i]["id"])
        else:
            prev_exit = s_node
    coords.append((sx, sy))
    line = LineString(coords).simplify(0.2)
    outside = line.difference(grid.passable_geom.buffer(0.05)).length
    # coverage of our own targets (within 2 m)
    cov = {}
    for lay in ("inspection", "waste"):
        g = layers.get(lay)
        if g is not None and len(g):
            cov[lay] = f"{int((g.geometry.centroid.distance(line) <= VISIT_M).sum())}/{len(g)}"
    from shapely.prepared import prep
    zone = line.buffer(VISIT_M, resolution=4)
    rows_cov = int(sum(1 for r in rowsg.geometry if r.intersection(zone).length > 0.8 * r.length))
    props = dict(length_m=round(line.length, 1), walk_minutes_4kmh=round(line.length / 4000 * 60, 1),
                 outside_passable_m=round(outside, 1),
                 outside_passable_pct=round(100 * outside / max(line.length, 1e-9), 2),
                 blocks=len(blocks), lanes=sum(b["lanes"] for b in blocks),
                 rows_covered=f"{rows_cov}/{len(rowsg)}", inspection_covered=cov.get("inspection", ""),
                 waste_covered=cov.get("waste", ""), visit_order=",".join(map(str, visited_order)),
                 solver="lawnmower (boustrophedon) sweeps + OR-Tools block order + Viterbi sweep variants",
                 start="organiser start point" if not hasattr(start_path, "x") else "user start point")
    log(f"lawnmower route {props['length_m']} m ({props['walk_minutes_4kmh']} min), rows covered {props['rows_covered']}, "
        f"inspection {props['inspection_covered']}, waste {props['waste_covered']}, "
        f"{props['outside_passable_pct']} % outside passable ground, {time.time() - t0:.0f} s")
    g = gpd.GeoDataFrame([dict(props, geometry=line)], geometry="geometry", crs=work_crs())
    g.attrs["start"] = (sx, sy)
    return g
