"""State of the geotech-model service: per-tile detection cache + site-wide ID stitching,
measurements and the farmer / auditor routes. All geometry is kept in EPSG:32635 metres.

Cross-tile IDs (brief §1): every tile is detected with a 6 m context from its neighbouring tiles,
results are clipped to the tile, then a site-wide pass merges block pieces that touch across tile
edges with the same row direction into one vineyard_id, and row pieces of a block that lie on
the same row axis (same across-row offset) into one row_id. IDs are re-derived after every
detect / delete, sorted north->south, west->east (they may change when tiles are added).
"""
import glob
import math
import os
import pickle
import re
import threading
import time

import numpy as np
from shapely.affinity import affine_transform
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import unary_union

TILES = os.environ.get("TILES_DIR", "/data/tiles")
ROUTE = os.environ.get("ROUTE_DIR", "/data/route")
CACHE = os.environ.get("CACHE_DIR", "/data/cache")
CRS = "EPSG:32635"
CONTEXT_M = 6.0
MAX_TARGETS = 80
AUDIT_MAX = 40
MODEL_VERSION = "vine-classic-3.1.0"
_NAME = re.compile(r"r(\d+)_c(\d+)", re.I)
_LOCK = threading.RLock()
_SITE = None                                   # stitched site (rebuilt lazily)


# ------------------------------------------------------------------ helpers
def _tile_path(name):
    name = os.path.basename(name)
    p = os.path.join(TILES, name)
    return p if os.path.isfile(p) else None


def _tile_box(path):
    import rasterio
    with rasterio.open(path) as ds:
        b = ds.bounds
        return box(b.left, b.bottom, b.right, b.top), ds.transform


def _neighbours(name):
    m = _NAME.search(name)
    if not m:
        return []
    r, c = int(m.group(1)), int(m.group(2))
    out = []
    for p in glob.glob(os.path.join(TILES, "*.tif")):
        q = _NAME.search(os.path.basename(p))
        if q and max(abs(int(q.group(1)) - r), abs(int(q.group(2)) - c)) == 1:
            out.append(p)
    return out


def _zones():
    """passages, forbidden, start (EPSG:32635)."""
    import geopandas as gpd
    from vine.route import load_zones
    files = [os.path.join(ROUTE, f) for f in ("passages.geojson", "forbidden.geojson") if os.path.exists(os.path.join(ROUTE, f))]
    passages, forbidden = load_zones(files) if files else (None, None)
    sp = os.path.join(ROUTE, "start.geojson")
    start = gpd.read_file(sp).to_crs(CRS).geometry.iloc[0] if os.path.exists(sp) else None
    return passages, forbidden, start


def _angle(lines):
    """Mean row direction (0..180 deg) of a set of lines."""
    v = np.zeros(2)
    for ln in lines:
        c = np.asarray(ln.coords)
        a = math.atan2(c[-1][1] - c[0][1], c[-1][0] - c[0][0])
        w = ln.length
        v += w * np.array([math.cos(2 * a), math.sin(2 * a)])
    return (math.degrees(math.atan2(v[1], v[0])) / 2) % 180 if v.any() else None


def _cpath(name):
    return os.path.join(CACHE, os.path.basename(name) + ".pkl")


# ------------------------------------------------------------------ detection
def detect_tile(name, force=False, log=lambda *a: None):
    """Run the pipeline on one tile (+ 6 m context), cache world geometries. Returns the cache dict."""
    path = _tile_path(name)
    if path is None:
        raise FileNotFoundError(name)
    cp = _cpath(name)
    if not force and os.path.exists(cp) and os.path.getmtime(cp) >= os.path.getmtime(path):
        with open(cp, "rb") as fh:
            return pickle.load(fh)
    from vine.common import set_crs
    from vine.source import MosaicSource
    from vine.candidates import find_candidates
    from vine.rows import process_region
    from vine.assemble import assemble, build_tables
    from vine.waste import detect_waste
    t0 = time.time()
    tb, _ = _tile_box(path)
    set_crs(CRS)
    src = MosaicSource([path] + _neighbours(name), aoi=tb.buffer(CONTEXT_M, join_style=2))
    passages, forbidden, _ = _zones()
    cands, _ = find_candidates(src, log)
    results = [process_region(src, c["geometry"], c["freq_angle"], log) for c in cands]
    blocks = assemble(results, passages=passages)
    canopy, rows, interrow, inspect = build_tables(blocks)
    waste = []
    if blocks:
        zone = unary_union([b["geometry"] for b in blocks]).buffer(20).intersection(tb)
        excl = [g for g in (forbidden, passages.buffer(1.0) if passages is not None else None) if g is not None]
        waste = detect_waste(src, zone, [r["geometry"] for r in rows], log,
                             exclude=unary_union(excl) if excl else None)

    def clip(recs):
        out = []
        for r in recs:
            g = r["geometry"].intersection(tb)
            if g.is_empty:
                continue
            parts = [p for p in getattr(g, "geoms", [g]) if p.geom_type == r["geometry"].geom_type or
                     (r["geometry"].geom_type.endswith("Polygon") and p.geom_type == "Polygon") or
                     (r["geometry"].geom_type == "LineString" and p.geom_type == "LineString")]
            for p in parts:
                if (p.geom_type == "Polygon" and p.area < 0.02) or (p.geom_type == "LineString" and p.length < 0.5):
                    continue
                out.append(dict({k: v for k, v in r.items() if k not in ("geometry", "gap_lines")}, geometry=p))
        return out
    blk_angle = {}
    for b in blocks:
        blk_angle[b["vineyard_id"]] = _angle([r["geometry"] for r in b["rows"]])
    d = dict(tile=os.path.basename(name), box=tb, elapsed_s=round(time.time() - t0, 1),
             blocks=[dict(vid=b["vineyard_id"], geometry=b["geometry"].intersection(tb), angle=blk_angle[b["vineyard_id"]],
                          spacing=float(b.get("period", 2.5))) for b in blocks if not b["geometry"].intersection(tb).is_empty],
             canopies=clip(canopy), rows=clip(rows), interrows=clip(interrow),
             gaps=[g for g in inspect if tb.contains(g["geometry"])],
             waste=[dict(kind=w["kind"], geometry=w["geometry"],
                         confidence=round(min(0.95, (0.6 if w["kind"] == "vivid" else 0.5) + min(w.get("area_px_m2", 0), 2) / 10), 2))
                    for w in waste if tb.intersects(w["geometry"].centroid)])
    os.makedirs(CACHE, exist_ok=True)
    with open(cp, "wb") as fh:
        pickle.dump(d, fh)
    global _SITE
    with _LOCK:
        _SITE = None
    log(f"{name}: {len(d['rows'])} rows, {len(d['canopies'])} canopies, {len(d['waste'])} waste in {d['elapsed_s']} s")
    return d


def delete_tile(name):
    global _SITE
    cp = _cpath(name)
    if not os.path.exists(cp):
        return False
    os.remove(cp)
    with _LOCK:
        _SITE = None
    return True


def cached_tiles():
    return sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(CACHE, "*.tif.pkl")))


# ------------------------------------------------------------------ site-wide stitching
def site():
    """Stitch all cached tiles: global vineyard_id / row_id for every object."""
    global _SITE
    with _LOCK:
        if _SITE is not None:
            return _SITE
        T = {}
        for n in cached_tiles():
            with open(_cpath(n), "rb") as fh:
                T[n] = pickle.load(fh)
        # --- blocks: union-find over tile pieces that touch with a similar row angle
        pieces = [(n, b) for n, d in T.items() for b in d["blocks"]]
        parent = list(range(len(pieces)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        from shapely.strtree import STRtree
        geoms = [b["geometry"].buffer(1.5) for _, b in pieces]
        tree = STRtree(geoms) if geoms else None
        for i, (n, b) in enumerate(pieces):
            for j in (tree.query(geoms[i]) if tree is not None else []):
                j = int(j)
                if j <= i or pieces[j][0] == n or not geoms[i].intersects(geoms[j]):
                    continue
                a1, a2 = b["angle"], pieces[j][1]["angle"]
                if a1 is None or a2 is None or min(abs(a1 - a2), 180 - abs(a1 - a2)) <= 6:
                    parent[find(i)] = find(j)
        comp = {}
        for i in range(len(pieces)):
            comp.setdefault(find(i), []).append(i)
        blocks = []
        for members in comp.values():
            g = unary_union([pieces[i][1]["geometry"] for i in members])
            blocks.append(dict(members=members, geometry=g))
        blocks.sort(key=lambda b: (-round(b["geometry"].centroid.y / 25), b["geometry"].centroid.x))
        vmap = {}
        for k, b in enumerate(blocks, 1):
            b["vineyard_id"] = f"V{k:02d}"
            for i in b["members"]:
                vmap[(pieces[i][0], pieces[i][1]["vid"])] = b["vineyard_id"]
        # --- rows: pieces of a block on the same axis (across-row offset) -> one row
        rows_by_v = {}
        for n, d in T.items():
            for r in d["rows"]:
                v = vmap.get((n, r["vineyard_id"]))
                if v:
                    rows_by_v.setdefault(v, []).append((n, r))
        rmap = {}
        row_conf = {}
        row_struct = {}
        for v, lst in rows_by_v.items():
            ang = _angle([r["geometry"] for _, r in lst]) or 0.0
            a = math.radians(ang)
            nrm = np.array([-math.sin(a), math.cos(a)])
            sp = np.median([b["spacing"] for n, d in T.items() for b in d["blocks"] if vmap.get((n, b["vid"])) == v] or [2.5])
            offs = sorted((float(np.dot(np.asarray(r["geometry"].interpolate(0.5, normalized=True).coords[0]), nrm)), k)
                          for k, (_, r) in enumerate(lst))
            groups, cur = [], [offs[0]]
            for o in offs[1:]:
                if o[0] - cur[-1][0] > 0.4 * sp:
                    groups.append(cur)
                    cur = [o]
                else:
                    cur.append(o)
            groups.append(cur)
            for gi, grp in enumerate(groups, 1):
                rid = f"{v}-R{gi:03d}"
                sts = [lst[k][1].get("row_structure") for _, k in grp]
                row_struct[rid] = "disrupted" if "disrupted" in sts else ("regular" if "regular" in sts else "unassessable")
                row_conf[rid] = round(float(np.mean([lst[k][1].get("confidence", 0.7) for _, k in grp])), 3)
                for _, k in grp:
                    n, r = lst[k]
                    rmap[(n, r["row_id"])] = rid
        _SITE = dict(tiles=T, blocks=blocks, vmap=vmap, rmap=rmap, row_conf=row_conf, row_struct=row_struct)
        return _SITE


def _ids(S, n, rec):
    v = S["vmap"].get((n, rec.get("vineyard_id")), "")
    r = S["rmap"].get((n, rec.get("row_id")), "") if rec.get("row_id") else ""
    return v, r


# ------------------------------------------------------------------ /v1/detect payload
def tile_payload(name):
    S = site()
    d = S["tiles"].get(os.path.basename(name))
    if d is None:
        return None
    _, tr = _tile_box(_tile_path(name) or name) if _tile_path(name) else (None, None)
    inv = ~tr
    px = lambda g: [[round(x, 1), round(y, 1)] for x, y in (inv * c for c in g.coords)]   # noqa: E731
    out = dict(canopies=[], rows=[], interrows=[], waste=[])
    for c in d["canopies"]:
        v, _ = _ids(S, d["tile"], c)
        out["canopies"].append(dict(points=px(c["geometry"].exterior)[:-1], vineyard_id=v,
                                    confidence=round(float(c.get("confidence", 0.7)), 3)))
    for r in d["rows"]:
        v, rid = _ids(S, d["tile"], r)
        out["rows"].append(dict(points=px(r["geometry"]), vineyard_id=v, row_id=rid,
                                row_structure=S["row_struct"].get(rid, r.get("row_structure")),
                                confidence=round(float(r.get("confidence", 0.7)), 3)))
    for i in d["interrows"]:
        v, _ = _ids(S, d["tile"], i)
        out["interrows"].append(dict(points=px(i["geometry"].exterior)[:-1], vineyard_id=v,
                                     interrow_cover=i.get("interrow_cover"), confidence=round(float(i.get("confidence", 0.7)), 3)))
    for w in d["waste"]:
        x0, y0, x1, y1 = w["geometry"].bounds
        (a, b), (c_, e) = inv * (x0, y1), inv * (x1, y0)
        near = sorted((bl["geometry"].distance(w["geometry"]), bl["vineyard_id"]) for bl in S["blocks"])
        out["waste"].append(dict(bbox=[round(a, 1), round(b, 1), round(c_, 1), round(e, 1)],
                                 vineyard_id=near[0][1] if near and near[0][0] <= 10 else "",
                                 confidence=w["confidence"]))
    return out


# ------------------------------------------------------------------ scope
def _scope(tiles, area_world):
    S = site()
    names = [os.path.basename(t) for t in tiles] if tiles else list(S["tiles"])
    missing = [t for t in names if t not in S["tiles"]]
    names = [t for t in names if t in S["tiles"]]
    geom = unary_union([S["tiles"][n]["box"] for n in names]) if names else None
    if area_world and len(area_world) >= 3 and geom is not None:
        geom = geom.intersection(Polygon(area_world).buffer(0))
    return S, names, geom, missing


def _in(g, scope):
    return scope is not None and g.intersects(scope)


# ------------------------------------------------------------------ /v1/measurements
def measurements(tiles=None, area_world=None):
    S, names, scope, _ = _scope(tiles, area_world)
    per = {}
    for n in names:
        d = S["tiles"][n]
        for c in d["canopies"]:
            g = c["geometry"].intersection(scope)
            if not g.is_empty:
                per.setdefault(_ids(S, n, c)[0], dict(can=[], ir=[], rows={}))["can"].append(g)
        for i in d["interrows"]:
            g = i["geometry"].intersection(scope)
            if not g.is_empty:
                per.setdefault(_ids(S, n, i)[0], dict(can=[], ir=[], rows={}))["ir"].append(g)
        for r in d["rows"]:
            g = r["geometry"].intersection(scope)
            if not g.is_empty:
                v, rid = _ids(S, n, r)
                per.setdefault(v, dict(can=[], ir=[], rows={}))["rows"][rid] = \
                    per[v]["rows"].get(rid, 0.0) + g.length
    blocks = []
    for v in sorted(k for k in per if k):
        p = per[v]
        cu = unary_union(p["can"]) if p["can"] else Polygon()
        iu = unary_union(p["ir"]) if p["ir"] else Polygon()
        rows = [dict(row_id=k, length_m=round(L, 2)) for k, L in sorted(p["rows"].items())]
        blocks.append(dict(vineyard_id=v, canopy_area_m2=round(cu.area, 2), canopy_area_ha=round(cu.area / 1e4, 5),
                           interrow_area_m2=round(iu.area, 2), interrow_area_ha=round(iu.area / 1e4, 5),
                           canopy_count=len(getattr(cu, "geoms", [cu])) if not cu.is_empty else 0,
                           row_count=len(rows), total_row_length_m=round(sum(r["length_m"] for r in rows), 2), rows=rows))
    waste = sum(1 for n in names for w in S["tiles"][n]["waste"] if _in(w["geometry"], scope))
    ca = sum(b["canopy_area_m2"] for b in blocks)
    ia = sum(b["interrow_area_m2"] for b in blocks)
    return dict(block_count=len(blocks), row_count=sum(b["row_count"] for b in blocks),
                canopy_area_m2=round(ca, 2), canopy_area_ha=round(ca / 1e4, 5),
                interrow_area_m2=round(ia, 2), interrow_area_ha=round(ia / 1e4, 5),
                total_row_length_m=round(sum(b["total_row_length_m"] for b in blocks), 2),
                waste_count=waste, tiles_loaded=len(names), blocks=blocks)


# ------------------------------------------------------------------ targets
def targets(role, S, names, scope):
    out = []
    if role == "farmer":
        k = 0
        for n in names:
            for g in S["tiles"][n]["gaps"]:
                if not _in(g["geometry"], scope):
                    continue
                v, rid = _ids(S, n, g)
                k += 1
                out.append(dict(id=f"gap-{rid or v}-{k}", kind="inspection", point_world=[g["x"], g["y"]], vineyard_id=v,
                                row_id=rid, source_tile=n, reason=f"{g['gap_m']:.1f} m gap in row",
                                priority=1.0 + min(float(g["gap_m"]), 30.0) / 10.0))
            for j, w in enumerate(S["tiles"][n]["waste"], 1):
                c = w["geometry"].centroid
                if not _in(c, scope):
                    continue
                near = sorted((bl["geometry"].distance(c), bl["vineyard_id"]) for bl in S["blocks"])
                out.append(dict(id=f"waste-{n[:-4]}-{j}", kind="waste", point_world=[round(c.x, 2), round(c.y, 2)],
                                vineyard_id=near[0][1] if near and near[0][0] <= 10 else "", row_id="", source_tile=n,
                                reason=f"waste object to collect ({w['kind']} cue, confidence {w['confidence']:.2f})",
                                priority=3.0))
        return out
    # auditor: verify counts / lengths / areas where the model is least sure, plus a representative
    # sample of every block. Per block: the 2 lowest-confidence rows, the median row (sample), and the
    # lowest-confidence inter-row. Priority = 1 + 2 x (1 - confidence). Capped at AUDIT_MAX by priority.
    rows = {}
    for n in names:
        for r in S["tiles"][n]["rows"]:
            v, rid = _ids(S, n, r)
            g = r["geometry"].intersection(scope)
            if rid and not g.is_empty:
                rows.setdefault(v, {}).setdefault(rid, []).append((n, g))
    irs = {}
    for n in names:
        for i in S["tiles"][n]["interrows"]:
            if _in(i["geometry"], scope):
                irs.setdefault(_ids(S, n, i)[0], []).append((n, i))
    for v, rr in rows.items():
        ids = sorted(rr, key=lambda k: S["row_conf"].get(k, 0.7))
        pick = ids[:2] + ([sorted(rr)[len(rr) // 2]] if len(rr) > 2 else [])
        for rid in dict.fromkeys(pick):
            n, g = max(rr[rid], key=lambda t: t[1].length)
            p = g.interpolate(0.5, normalized=True)
            conf = S["row_conf"].get(rid, 0.7)
            L = sum(x[1].length for x in rr[rid])
            why = "low confidence" if rid in ids[:2] else "random sample"
            out.append(dict(id=f"audit-{rid}", kind="audit", point_world=[round(p.x, 2), round(p.y, 2)], vineyard_id=v,
                            row_id=rid, source_tile=n, priority=1.0 + 2 * (1 - conf),
                            reason=f"verify row {rid} ({why}): model length {L:.1f} m, "
                                   f"{S['row_struct'].get(rid)}, confidence {conf:.2f}"))
        if irs.get(v):
            n, i = min(irs[v], key=lambda t: t[1].get("confidence", 0.7))
            c = i["geometry"].representative_point()
            conf = float(i.get("confidence", 0.7))
            out.append(dict(id=f"audit-{v}-IR{len(out)}", kind="audit", point_world=[round(c.x, 2), round(c.y, 2)],
                            vineyard_id=v, row_id="", source_tile=n, priority=1.0 + 2 * (1 - conf),
                            reason=f"verify inter-row cover '{i.get('interrow_cover')}' and area "
                                   f"({i['geometry'].area:.0f} m2 here), confidence {conf:.2f}"))
    out.sort(key=lambda t: -t["priority"])
    return out[:AUDIT_MAX]


# ------------------------------------------------------------------ /v1/route
def route(role, tiles=None, area_world=None, start_world=None, solve_s=30, log=lambda *a: None):
    import geopandas as gpd
    from vine.common import set_crs
    from vine.route_opt import plan_route_optimal
    set_crs(CRS)
    S, names, scope, missing = _scope(tiles, area_world)
    if not names or scope is None or scope.is_empty:
        return dict(role=role, targets=[], route=None,
                    message="No detected tiles in this scope. Upload / detect tiles first." +
                            (f" Not detected: {', '.join(missing)}" if missing else ""))
    T = targets(role, S, names, scope)
    base = dict(role=role, targets=[{k: v for k, v in t.items() if k != "priority"} for t in T])
    if not T:
        return dict(base, route=None, message="No targets in this scope (no row gaps or waste found)." if role == "farmer"
                    else "No detected rows in this scope to audit.")
    if len(T) > MAX_TARGETS:
        return dict(base, route=None, message=f"{len(T)} targets in this scope; the limit is {MAX_TARGETS}. "
                                              f"Please choose fewer tiles or draw a smaller area.")
    passages, forbidden, start0 = _zones()
    start = Point(*start_world) if start_world else start0
    if start is None:
        start = scope.centroid
    frame = unary_union([scope, start.buffer(1)]).envelope.buffer(60, join_style=2)
    pas = passages.intersection(frame) if passages is not None else frame.difference(
        unary_union([b["geometry"] for b in S["blocks"]]).buffer(0.3))
    forb = forbidden.intersection(frame) if forbidden is not None else None
    G = lambda recs: gpd.GeoDataFrame(recs, geometry="geometry", crs=CRS) if recs else \
        gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=CRS)          # noqa: E731
    ctx = frame
    ctx_tiles = [n for n, d in S["tiles"].items() if d["box"].intersects(ctx)]
    layers = dict(
        interrow_area=G([dict(vineyard_id=_ids(S, n, i)[0], geometry=i["geometry"]) for n in ctx_tiles
                         for i in S["tiles"][n]["interrows"]]),
        row=G([dict(vineyard_id=_ids(S, n, r)[0], row_id=_ids(S, n, r)[1], geometry=r["geometry"]) for n in ctx_tiles
               for r in S["tiles"][n]["rows"]]),
        inspection=G([dict(inspection_id=t["id"], priority=t["priority"], geometry=Point(*t["point_world"])) for t in T]),
        waste=G([]))
    t0 = time.time()
    try:
        r = plan_route_optimal(layers, start, pas, forb, None, log, solve_s=solve_s,
                               weight=lambda kind, rec: rec.get("priority", 1.0))
    except Exception as e:                                     # pragma: no cover
        return dict(base, route=None, message=f"No legal route found: {e}")
    p = r.iloc[0]
    order = [i for i in str(p["visit_order"]).split(",") if i]
    sd = r.attrs.get("start_dist", {})
    sep = 2 * sum(sd.get(i, 0.0) for i in order)
    L = float(p["length_m"])
    msg = None
    skipped = [t["id"] for t in T if t["id"] not in set(order)]
    if skipped:
        msg = (f"{len(skipped)} target(s) could not be reached on authorised passages / inter-rows: "
               + ", ".join(skipped[:10]) + ("..." if len(skipped) > 10 else ""))
    return dict(base, route=dict(
        order=order, length_m=round(L, 1),
        polyline_world=[[round(x, 2), round(y, 2)] for x, y in r.geometry.iloc[0].coords],
        comparison=dict(separate_trips_m=round(sep, 1), optimized_tour_m=round(L, 1), saved_m=round(sep - L, 1),
                        reduction_pct=round(100 * (sep - L) / sep, 1) if sep > 0 else 0.0),
        outside_passable_pct=float(p["outside_passable_pct"]), solve_s=round(time.time() - t0, 1)), message=msg)
