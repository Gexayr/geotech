"""Walking route: closed tour from the organiser start point through every reachable target
(inspection points + waste), respecting the scoring rule that at most 2 % of the length may lie
outside passable inter-row areas and authorised passages.

Cost grid (0.5 m):
  cost 1   : authorised passages, inter-row areas (slightly eroded so the path stays inside them)
  cost 30  : other open ground within 8 m of passable ground (only used to bridge short headlands
             between row ends and a road; kept tiny by the high cost)
  wall     : row canopies (every row axis incl. planting gaps - trellis wires), forbidden zones,
             no-data outside the orthomosaic
A target is reachable when a passable cell lies within 2 m of it (scoring: visited = route within 2 m).
Shortest paths: Dijkstra on the 8-connected grid (no corner cutting); tour: nearest neighbour +
2-opt + Or-opt on the shortest-path matrix; returns to the start point.
"""
import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy import ndimage as ndi
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, box
from shapely.ops import unary_union

from .common import work_crs

RES = 0.5
VISIT_M = 2.0
CONNECT_M = 5.0
CONNECT_COST = 150.0
MERGE_M = 1.5            # targets whose access points are this close are served by one stop
MAX_OUTSIDE = 0.018      # keep off-passable walking below 1.8 % (scoring limit: 2 %)
TSP_TIME_S = 20          # OR-Tools search time for the first solve

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    _HAS_ORTOOLS = True
except ImportError:                                       # pragma: no cover
    _HAS_ORTOOLS = False


def solve_tsp_ortools(D, time_s):
    n = len(D)
    Di = np.round(np.where(np.isfinite(D), D, 1e7) * 10).astype(np.int64)
    man = pywrapcp.RoutingIndexManager(n, 1, 0)
    rt = pywrapcp.RoutingModel(man)
    cb = rt.RegisterTransitCallback(lambda i, j: int(Di[man.IndexToNode(i), man.IndexToNode(j)]))
    rt.SetArcCostEvaluatorOfAllVehicles(cb)
    prm = pywrapcp.DefaultRoutingSearchParameters()
    prm.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    prm.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    prm.time_limit.FromMilliseconds(int(time_s * 1000))
    sol = rt.SolveWithParameters(prm)
    if sol is None:
        return None
    i, t = rt.Start(0), []
    while not rt.IsEnd(i):
        t.append(man.IndexToNode(i))
        i = sol.Value(rt.NextVar(i))
    return t + [0]


def solve_tsp_best(D, time_s=TSP_TIME_S):
    """Best of OR-Tools (guided local search) and the NN + 2-opt + Or-opt heuristic."""
    cands = [solve_tsp(D)]
    if _HAS_ORTOOLS and len(D) > 3:
        t = solve_tsp_ortools(D, time_s)
        if t:
            cands.append(t)
    return min(cands, key=lambda t: sum(D[a, b] for a, b in zip(t[:-1], t[1:])))


def coverage(ti, layers, passages, start, margin=40.0):
    """Area the route may use: the image extent, or (GeoJSON-only mode, ti=None) the extent of the
    vineyard layers, passages and start point plus a margin."""
    if ti is not None and getattr(ti, "tiles", None):
        return unary_union([box(b.left, b.bottom, b.right, b.top) for _, b in ti.tiles.values()])
    geoms = [g for k in ("blocks", "interrow_area", "row") if layers.get(k) is not None and len(layers[k])
             for g in [unary_union(list(layers[k].geometry))]]
    if passages is not None:
        geoms.append(passages)
    geoms.append(start.buffer(1))
    return box(*unary_union(geoms).bounds).buffer(margin, join_style=2)


def load_zones(paths):
    pas, forb = [], []
    for p in paths or []:
        z = gpd.read_file(p)
        if z.crs is not None and z.crs != work_crs():
            z = z.to_crs(work_crs())
        t = z["type"].astype(str).str.lower() if "type" in z else None
        if t is None:
            continue
        pas += list(z[t == "passage"].geometry)
        forb += list(z[t == "forbidden"].geometry)
    return (unary_union(pas) if pas else None), (unary_union(forb) if forb else None)


def _load_start(path):
    s = gpd.read_file(path)
    if s.crs is not None and s.crs != work_crs():
        s = s.to_crs(work_crs())
    return s.geometry.iloc[0]


def solve_tsp(D):
    n = len(D)
    if n <= 2:
        return list(range(n)) + [0]
    un = set(range(1, n))
    t = [0]
    while un:
        last = t[-1]
        nxt = min(un, key=lambda j: D[last, j])
        t.append(nxt)
        un.remove(nxt)
    t = np.array(t + [0])
    improved, it = True, 0
    while improved and it < 100:
        improved, it = False, it + 1
        for i in range(1, len(t) - 2):                       # 2-opt
            a, b = t[i - 1], t[i]
            c, d = t[i + 1:-1], t[i + 2:]
            delta = D[a, c] + D[b, d] - D[a, b] - D[c, d]
            j = int(np.argmin(delta))
            if delta[j] < -1e-6:
                t[i:i + j + 2] = t[i:i + j + 2][::-1]
                improved = True
        for L in (1, 2, 3):                                  # Or-opt
            i = 1
            while i + L < len(t):
                seg = t[i:i + L]
                p, q = t[i - 1], t[i + L]
                rem = D[p, seg[0]] + D[seg[-1], q] - D[p, q]
                rest = np.r_[t[:i], t[i + L:]]
                a_, b_ = rest[:-1], rest[1:]
                ins = D[a_, seg[0]] + D[seg[-1], b_] - D[a_, b_]
                insr = D[a_, seg[-1]] + D[seg[0], b_] - D[a_, b_]
                k1, k2 = int(np.argmin(ins)), int(np.argmin(insr))
                best, rev, k = (ins[k1], False, k1) if ins[k1] <= insr[k2] else (insr[k2], True, k2)
                if best < rem - 1e-6:
                    t = np.r_[rest[:k + 1], seg[::-1] if rev else seg, rest[k + 1:]]
                    improved = True
                else:
                    i += 1
    return list(t)


def plan_route(layers, start_path, passages, forbidden, ti, log=print):
    inter = layers["interrow_area"]
    rows = layers["row"]
    targets = []
    if len(layers["inspection"]) and "inspection_id" in layers["inspection"]:
        for r in layers["inspection"].itertuples():
            targets.append(dict(id=r.inspection_id, kind="inspection", geometry=r.geometry))
    if len(layers["waste"]) and "waste_id" in layers["waste"]:
        for r in layers["waste"].itertuples():
            targets.append(dict(id=r.waste_id, kind="waste", geometry=r.geometry.centroid))

    if start_path is not None and hasattr(start_path, "x"):
        start = start_path
        start_note = "user start point"
    elif start_path:
        start = _load_start(start_path)
        start_note = "organiser start point"
    else:
        start = unary_union(list(layers["blocks"].geometry)).centroid
        start_note = "PLACEHOLDER start (no start file)"
    sx, sy = start.x, start.y
    cov_geom = coverage(ti, layers, passages, start)
    passable_geom = unary_union([g for g in [passages, unary_union(list(inter.geometry)) if len(inter) else None] if g is not None])
    L, B, R, T = cov_geom.bounds
    W, H = int(np.ceil((R - L) / RES)), int(np.ceil((T - B) / RES))
    tr = from_origin(L, T, RES, RES)

    def rast(geoms, touched=False):
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        if not geoms:
            return np.zeros((H, W), bool)
        return rasterize([(g, 1) for g in geoms], out_shape=(H, W), transform=tr, all_touched=touched).astype(bool)

    cov = rast([cov_geom])
    wall = rast(list(rows.geometry.buffer(0.3, cap_style=2)) if len(rows) else [], touched=True)
    # planting gaps >= 5 m (missing vines) may be crossed: cheaper than walking round via a headland
    gl = layers.get("row_gaps")
    if gl is not None and len(gl):
        op = [LineString([g.interpolate(0.8), g.interpolate(g.length - 0.8)]).buffer(0.6, cap_style=2)
              for g in gl.geometry if g.length > 2.5]
        wall &= ~rast(op)
    if forbidden is not None:
        wall |= rast([forbidden], touched=True)
    good = rast([passages.buffer(-0.05)] if passages is not None else []) | rast(list(inter.geometry.buffer(-0.1)) if len(inter) else [])
    good |= rast([start.buffer(1.5)])
    good &= ~wall & cov
    near = ndi.distance_transform_edt(~good) * RES <= CONNECT_M
    free = (good | near) & ~wall & cov
    cost = np.where(good, 1.0, CONNECT_COST)

    idx = -np.ones((H, W), np.int64)
    fy, fx = np.nonzero(free)
    idx[fy, fx] = np.arange(len(fy))
    rs, cs, ws, gl = [], [], [], []
    for dy, dx, w in [(0, 1, 1.0), (1, 0, 1.0), (1, 1, 2 ** .5), (1, -1, 2 ** .5)]:
        y0, x0 = fy, fx
        y1, x1 = y0 + dy, x0 + dx
        ok = (y1 < H) & (x1 >= 0) & (x1 < W)
        y0, x0, y1, x1 = y0[ok], x0[ok], y1[ok], x1[ok]
        ok = free[y1, x1]
        if dy and dx:
            ok &= free[y0 + dy, x0] & free[y0, x0 + dx]
        y0, x0, y1, x1 = y0[ok], x0[ok], y1[ok], x1[ok]
        rs.append(idx[y0, x0])
        cs.append(idx[y1, x1])
        ws.append(w * RES * 0.5 * (cost[y0, x0] + cost[y1, x1]))
    G = coo_matrix((np.concatenate(ws), (np.concatenate(rs), np.concatenate(cs))), shape=(len(fy),) * 2).tocsr()
    log(f"route grid {W}x{H}: {len(fy)} walkable cells ({good.sum()} passable)")

    # snapping: targets need a passable cell within VISIT_M; the start may use connectors
    dg, (gy, gx) = ndi.distance_transform_edt(~good, return_indices=True)
    df, (qy, qx) = ndi.distance_transform_edt(~free, return_indices=True)

    def cell(x, y):
        c, r = int((x - L) / RES), int((T - y) / RES)
        return (r, c) if 0 <= r < H and 0 <= c < W else None

    rc = cell(sx, sy)
    if rc is None or df[rc] * RES > 10:
        raise SystemExit("start point is not near walkable ground")
    nodes = [idx[qy[rc], qx[rc]]]
    ok_t, unreach = [], []
    for t in targets:
        rc = cell(t["geometry"].x, t["geometry"].y)
        if rc is None or dg[rc] * RES > VISIT_M - 0.25:
            unreach.append(t)
            continue
        ok_t.append(t)
        nodes.append(idx[gy[rc], gx[rc]])
    d0 = dijkstra(G, directed=False, indices=nodes[0])
    keep = [i for i in range(1, len(nodes)) if np.isfinite(d0[nodes[i]])]
    unreach += [ok_t[i - 1] for i in range(1, len(nodes)) if i not in set(keep)]
    ok_t = [ok_t[i - 1] for i in keep]
    tcells = [nodes[i] for i in keep]
    # merge targets whose access cells are close: one visit covers all of them (visited = within 2 m)
    groups, gcell = [], []
    for t, c in zip(ok_t, tcells):
        p = np.array([fx[c], fy[c]])
        for gi, gc in enumerate(gcell):
            if np.hypot(*(p - np.array([fx[gc], fy[gc]]))) * RES <= MERGE_M:
                groups[gi].append(t)
                break
        else:
            groups.append([t])
            gcell.append(c)
    nodes = [nodes[0]] + gcell
    n = len(nodes)
    log(f"targets: {len(ok_t)} reachable ({n - 1} stops after merging), "
        f"{len(unreach)} not reachable within {VISIT_M} m of passable ground")

    # shortest-path costs + predecessor trees (kept, so legs are not recomputed)
    D = np.zeros((n, n))
    preds = np.zeros((n, G.shape[0]), np.int32)
    for s0 in range(0, n, 8):
        d, pr = dijkstra(G, directed=False, indices=nodes[s0:s0 + 8], return_predecessors=True)
        D[s0:s0 + 8] = d[:, nodes]
        preds[s0:s0 + 8] = pr
    D = np.minimum(D, D.T)

    def path(a_, b_):
        pr, out_, cur = preds[a_], [], nodes[b_]
        while cur != nodes[a_] and cur >= 0:
            out_.append(cur)
            cur = pr[cur]
        out_.append(nodes[a_])
        return out_[::-1]

    goodv = good[fy, fx]

    def leg_stats(a_, b_):
        pth = np.array(path(a_, b_))
        if len(pth) < 2:
            return pth, 0.0, 0.0
        st = np.hypot(np.diff(fx[pth]), np.diff(fy[pth])) * RES
        bad = ~(goodv[pth[:-1]] & goodv[pth[1:]])
        return pth, float(st.sum()), float(st[bad].sum())

    active = list(range(1, n))
    skipped = []
    for it in range(200):
        sub = [0] + active
        tour = [sub[i] for i in solve_tsp_best(D[np.ix_(sub, sub)], TSP_TIME_S if it == 0 else 3)]
        stats = {(a_, b_): leg_stats(a_, b_) for a_, b_ in zip(tour[:-1], tour[1:])}
        tot = sum(v[1] for v in stats.values())
        out = sum(v[2] for v in stats.values())
        if it % 10 == 0 or out / max(tot, 1e-9) <= MAX_OUTSIDE:
            log(f"   route iter {it}: {len(active)} stops, {tot:.0f} m, off-passable {out:.0f} m ({100 * out / max(tot, 1e-9):.2f} %)")
        if tot == 0 or out / tot <= MAX_OUTSIDE or not active:
            break
        # drop the stops whose removal saves the most off-passable walking (several per round when far
        # over budget); if no single removal helps, drop the stop with most off-passable access walking
        cand = []
        for k in range(1, len(tour) - 1):
            a_, v_, b_ = tour[k - 1], tour[k], tour[k + 1]
            adj = stats[(a_, v_)][2] + stats[(v_, b_)][2]
            cand.append((adj - leg_stats(a_, b_)[2], adj, v_))
        if not cand:
            break
        cand.sort(reverse=True)
        if cand[0][0] <= 0:
            cand.sort(key=lambda c: -c[1])
            if cand[0][1] <= 0:
                log(f"   route iter {it}: remaining off-passable walking is not caused by any target")
                break
        k_drop = max(1, min(5, int((out / tot - MAX_OUTSIDE) * 100)))
        for c in cand[:k_drop]:
            active.remove(c[2])
            skipped += groups[c[2] - 1]
    if skipped:
        log(f"{len(skipped)} target(s) skipped to keep the route within {100 * MAX_OUTSIDE:.1f} % off passable ground")

    coords = [(sx, sy)]
    legs = []
    ids = ["START"] + ["+".join(t["id"] for t in g) for g in groups]
    for a_, b_ in zip(tour[:-1], tour[1:]):
        pth = stats[(a_, b_)][0]
        seg = [(L + (fx[p] + .5) * RES, T - (fy[p] + .5) * RES) for p in pth]
        coords.extend(seg)
        legs.append(dict(frm=ids[a_].split("+")[0], to=ids[b_].split("+")[0], coords=seg,
                         covers=ids[b_].split("+") if b_ else []))
    coords.append((sx, sy))
    line = LineString(coords).simplify(0.2)
    outside = line.difference(passable_geom.buffer(0.05)).length
    visited = [t for v in tour[1:-1] for t in groups[v - 1]]
    order = [t["id"] for t in visited]
    props = dict(length_m=round(line.length, 1), n_targets=len(visited), visit_order=",".join(order),
                 unreachable=",".join(t["id"] for t in unreach),
                 skipped=",".join(t["id"] for t in skipped), start=start_note,
                 walk_minutes_4kmh=round(line.length / 4000 * 60, 1),
                 outside_passable_m=round(outside, 1),
                 outside_passable_pct=round(100 * outside / max(line.length, 1e-9), 2),
                 solver="OR-Tools guided local search" if _HAS_ORTOOLS else "NN + 2-opt + Or-opt",
                 passable_rule=f"authorised passages + inter-row areas (connectors <= {CONNECT_M} m, cost x{CONNECT_COST:g})")
    log(f"route {props['length_m']} m ({props['walk_minutes_4kmh']} min at 4 km/h) through {len(visited)} targets, "
        f"{props['outside_passable_pct']} % outside passable ground; {start_note}")
    g = gpd.GeoDataFrame([dict(props, geometry=line)], geometry="geometry", crs=work_crs())
    g.attrs["start"] = (sx, sy)
    g.attrs["legs"] = legs
    g.attrs["targets"] = {t["id"]: t for t in ok_t}
    g.attrs["unreachable"] = unreach + skipped
    return g


class Grid:
    """Walkability grid shared by the planners (same rules as plan_route)."""

    def __init__(self, layers, passages, forbidden, ti, start, log=print, res=None):
        from types import SimpleNamespace  # noqa: F401
        inter, rows = layers["interrow_area"], layers["row"]
        cov_geom = coverage(ti, layers, passages, start)
        self.res = res or RES
        self.passable_geom = unary_union([g for g in [passages, unary_union(list(inter.geometry)) if len(inter) else None]
                                          if g is not None])
        L, B, R, T = cov_geom.bounds
        W, H = int(np.ceil((R - L) / self.res)), int(np.ceil((T - B) / self.res))
        tr = from_origin(L, T, self.res, self.res)

        def rast(geoms, touched=False):
            geoms = [g for g in geoms if g is not None and not g.is_empty]
            if not geoms:
                return np.zeros((H, W), bool)
            return rasterize([(g, 1) for g in geoms], out_shape=(H, W), transform=tr, all_touched=touched).astype(bool)
        cov = rast([cov_geom])
        wall = rast(list(rows.geometry.buffer(0.3, cap_style=2)) if len(rows) else [], touched=True)
        gl = layers.get("row_gaps")
        if gl is not None and len(gl):
            wall &= ~rast([LineString([g.interpolate(0.8), g.interpolate(g.length - 0.8)]).buffer(0.6, cap_style=2)
                           for g in gl.geometry if g.length > 2.5])
        if forbidden is not None:
            wall |= rast([forbidden], touched=True)
        good = rast([passages.buffer(-0.05)] if passages is not None else []) | \
            rast(list(inter.geometry.buffer(-0.1)) if len(inter) else [])
        good |= rast([start.buffer(1.5)])
        good &= ~wall & cov
        near = ndi.distance_transform_edt(~good) * self.res <= CONNECT_M
        free = (good | near) & ~wall & cov
        cost = np.where(good, 1.0, CONNECT_COST)
        idx = -np.ones((H, W), np.int64)
        fy, fx = np.nonzero(free)
        idx[fy, fx] = np.arange(len(fy))
        rs, cs, ws = [], [], []
        for dy, dx, w in [(0, 1, 1.0), (1, 0, 1.0), (1, 1, 2 ** .5), (1, -1, 2 ** .5)]:
            y0, x0 = fy, fx
            y1, x1 = y0 + dy, x0 + dx
            ok = (y1 < H) & (x1 >= 0) & (x1 < W)
            y0, x0, y1, x1 = y0[ok], x0[ok], y1[ok], x1[ok]
            ok = free[y1, x1]
            if dy and dx:
                ok &= free[y0 + dy, x0] & free[y0, x0 + dx]
            y0, x0, y1, x1 = y0[ok], x0[ok], y1[ok], x1[ok]
            rs.append(idx[y0, x0])
            cs.append(idx[y1, x1])
            ws.append(w * self.res * 0.5 * (cost[y0, x0] + cost[y1, x1]))
        self.G = coo_matrix((np.concatenate(ws), (np.concatenate(rs), np.concatenate(cs))),
                            shape=(len(fy),) * 2).tocsr()
        self.good, self.free, self.idx, self.fx, self.fy = good, free, idx, fx, fy
        self.L, self.T, self.W, self.H = L, T, W, H
        self.goodv = good[fy, fx]
        _, (self.gy, self.gx) = ndi.distance_transform_edt(~good, return_indices=True)
        self.dg = ndi.distance_transform_edt(~good) * self.res
        _, (self.qy, self.qx) = ndi.distance_transform_edt(~free, return_indices=True)
        log(f"route grid {W}x{H}: {len(fy)} walkable cells ({good.sum()} passable)")

    def rc(self, x, y):
        c, r = int((x - self.L) / self.res), int((self.T - y) / self.res)
        return (min(max(r, 0), self.H - 1), min(max(c, 0), self.W - 1))

    def node(self, x, y, passable=True):
        r, c = self.rc(x, y)
        if passable:
            return int(self.idx[self.gy[r, c], self.gx[r, c]])
        return int(self.idx[self.qy[r, c], self.qx[r, c]])

    def xy(self, n):
        return self.L + (self.fx[n] + .5) * self.res, self.T - (self.fy[n] + .5) * self.res

    def path(self, pred, a, b):
        out, cur = [], b
        while cur != a and cur >= 0:
            out.append(cur)
            cur = pred[cur]
        out.append(a)
        return out[::-1]

    def leg(self, a, b, limit=np.inf):
        d, pr = dijkstra(self.G, directed=False, indices=a, return_predecessors=True, limit=limit)
        if not np.isfinite(d[b]):
            if np.isfinite(limit):
                return self.leg(a, b)
            return None
        return self.path(pr, a, b)
