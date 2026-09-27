"""Stage 3 - merge per-region results into globally consistent objects, IDs and measurements."""
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import nearest_points, unary_union


def _polys(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def no_holes(p):
    """CVAT polygons cannot have holes: cut a 2 cm slit from every hole to the outer ring."""
    from shapely.ops import nearest_points
    if not isinstance(p, Polygon) or not p.interiors:
        return [p]
    q = p
    for ring in p.interiors:
        h = Polygon(ring)
        a_, b_ = nearest_points(h.representative_point(), p.exterior)
        q = q.difference(LineString([h.centroid, b_]).buffer(0.01, cap_style=2))
    return [x for x in _polys(q) if x.area > 0.01]


def assemble(region_results, min_rows=3, passages=None, merge_dist=5.0):
    """region_results: list of dicts from rows.process_region (None entries allowed).
    Rules 6: plantings < 5 m apart are one block unless a road / track (passage) separates them."""
    blocks = []
    for ri, res in enumerate(region_results):
        if not res:
            continue
        P = res["period"]
        by = {}
        for r in res["rows"]:
            by.setdefault(r["block"], []).append(r)
        for b, rows in by.items():
            ir = [i for i in res["interrows"] if i["block"] == b]
            shape = unary_union([r["geometry"].buffer(0.55 * P, cap_style=2) for r in rows])
            shape = shape.buffer(1.0).buffer(-1.0)
            blocks.append(dict(region=ri, rows=rows, interrows=ir, geometry=shape, period=P,
                               vine_spacing=res["vine_spacing"],
                               row_len=sum(r["geometry"].length for r in rows)))
    # de-duplicate blocks found from two overlapping candidate regions: keep the better supported one
    blocks.sort(key=lambda b: -b["row_len"])
    kept = []
    for b in blocks:
        if any(b["geometry"].intersection(k["geometry"]).area > 0.3 * min(b["geometry"].area, k["geometry"].area)
               for k in kept):
            continue
        kept.append(b)
    # merge plantings closer than merge_dist that are not separated by a passage
    parent = list(range(len(kept)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            gi, gj = kept[i]["geometry"], kept[j]["geometry"]
            if kept[i]["region"] == kept[j]["region"]:      # already separated by a detected road / track
                continue
            if gi.distance(gj) >= merge_dist:
                continue
            if passages is not None:
                a_, b_ = nearest_points(gi, gj)
                link = LineString([a_, b_]) if a_.distance(b_) > 0.01 else a_.buffer(0.5)
                if link.intersects(passages):
                    continue
            parent[find(i)] = find(j)
    groups = {}
    for i, b in enumerate(kept):
        groups.setdefault(find(i), []).append(b)
    merged = []
    for g in groups.values():
        if len(g) == 1:
            merged.append(g[0])
            continue
        merged.append(dict(region=g[0]["region"], rows=[r for b in g for r in b["rows"]],
                           interrows=[i for b in g for i in b["interrows"]],
                           geometry=unary_union([b["geometry"] for b in g]),
                           period=float(np.median([b["period"] for b in g])),
                           vine_spacing=float(np.median([b["vine_spacing"] for b in g])),
                           row_len=sum(b["row_len"] for b in g)))
    # garden vineyards count only with >= 3 rows (rules 2.3)
    merged = [b for b in merged if len(b["rows"]) >= min_rows]
    # IDs: blocks north -> south, rows across the block
    merged.sort(key=lambda b: (-b["geometry"].centroid.y, b["geometry"].centroid.x))
    for vid, b in enumerate(merged, 1):
        v = f"V{vid:02d}"
        b["vineyard_id"] = v
        # order rows by their offset across the block (perpendicular to the mean row direction)
        d = np.array([np.subtract(*np.asarray(r["geometry"].coords)[[-1, 0]]) for r in b["rows"]])
        d = np.where((d[:, :1] < 0), -d, d).mean(0)
        nrm = np.array([-d[1], d[0]]) / (np.hypot(*d) + 1e-9)
        b["rows"].sort(key=lambda r: -float(np.dot(np.asarray(r["geometry"].centroid.coords[0]), nrm)))
        for k, r in enumerate(b["rows"], 1):
            r["row_id"] = f"{v}-R{k:03d}"
            r["vineyard_id"] = v
        for ir in b["interrows"]:
            ir["vineyard_id"] = v
    return merged


def build_tables(blocks):
    """Return GeoDataFrame-ready lists + measurement DataFrame (EPSG:32635, metres)."""
    canopy, rows, interrow, inspect = [], [], [], []
    iid = 0
    from shapely.strtree import STRtree
    all_vines = [v for b in blocks for r in b["rows"] for v in r["vines"]]
    vtree = STRtree(all_vines) if all_vines else None
    for b in blocks:
        vid = b["vineyard_id"]
        for r in b["rows"]:
            rows.append(dict(vineyard_id=vid, row_id=r["row_id"], row_structure=r["structure"],
                             length_m=round(r["geometry"].length, 2), confidence=r.get("conf", 0.7),
                             geometry=r["geometry"],
                             gap_lines=r.get("gap_lines", [])))
            for v in r["vines"]:
                for p in _polys(v):
                    canopy.append(dict(vineyard_id=vid, row_id=r["row_id"], confidence=r.get("conf", 0.7), geometry=p))
            for pt, glen in r["gap_pts"]:
                iid += 1
                inspect.append(dict(inspection_id=f"I{iid:04d}", type="row_gap", vineyard_id=vid,
                                    row_id=r["row_id"], gap_m=round(glen, 2), x=round(pt.x, 2),
                                    y=round(pt.y, 2), geometry=pt))
        # inter-row polygons must not overlap any canopy (also canopy of a neighbouring block)
        for ir in b["interrows"]:
            near = [all_vines[j] for j in vtree.query(ir["geometry"])] if vtree is not None else []
            g = ir["geometry"].difference(unary_union(near)) if near else ir["geometry"]
            for p in [x for q in _polys(g) for x in no_holes(q)]:
                if p.area < 0.5:
                    continue
                interrow.append(dict(vineyard_id=vid, interrow_cover=ir["cover"],
                                     veg_frac=ir["veg_frac"], confidence=round(float(min(1.0, 0.55 + 2 * min(
                                         abs(ir["veg_frac"] - 0.25), abs(ir["veg_frac"] - 0.75)))), 3), geometry=p))
    return canopy, rows, interrow, inspect


def measurements(blocks, canopy, rows, interrow):
    recs = []
    can_by = {}
    for c in canopy:
        can_by.setdefault(c["vineyard_id"], []).append(c["geometry"])
    ir_by = {}
    for i in interrow:
        ir_by.setdefault(i["vineyard_id"], []).append(i["geometry"])
    tot_can = unary_union([c["geometry"] for c in canopy]).area if canopy else 0
    tot_ir = unary_union([i["geometry"] for i in interrow]).area if interrow else 0
    tot_len = sum(r["length_m"] for r in rows)
    recs.append(dict(level="total", vineyard_id="", row_id="", n_blocks=len(blocks), n_rows=len(rows),
                     row_length_m=round(tot_len, 2),
                     canopy_area_m2=round(tot_can, 2), canopy_area_ha=round(tot_can / 1e4, 4),
                     interrow_area_m2=round(tot_ir, 2), interrow_area_ha=round(tot_ir / 1e4, 4),
                     row_structure=""))
    for b in blocks:
        vid = b["vineyard_id"]
        br = [r for r in rows if r["vineyard_id"] == vid]
        ca = unary_union(can_by.get(vid, [])).area if can_by.get(vid) else 0
        ia = unary_union(ir_by.get(vid, [])).area if ir_by.get(vid) else 0
        recs.append(dict(level="block", vineyard_id=vid, row_id="", n_blocks=1, n_rows=len(br),
                         row_length_m=round(sum(r["length_m"] for r in br), 2),
                         canopy_area_m2=round(ca, 2), canopy_area_ha=round(ca / 1e4, 4),
                         interrow_area_m2=round(ia, 2), interrow_area_ha=round(ia / 1e4, 4),
                         row_structure=""))
    rc = {}
    for c in canopy:
        rc.setdefault(c["row_id"], []).append(c["geometry"])
    for r in rows:
        ca = unary_union(rc.get(r["row_id"], [])).area if rc.get(r["row_id"]) else 0
        recs.append(dict(level="row", vineyard_id=r["vineyard_id"], row_id=r["row_id"], n_blocks="",
                         n_rows=1, row_length_m=r["length_m"], canopy_area_m2=round(ca, 2),
                         canopy_area_ha=round(ca / 1e4, 4), interrow_area_m2="", interrow_area_ha="",
                         row_structure=r["row_structure"]))
    return pd.DataFrame(recs)
