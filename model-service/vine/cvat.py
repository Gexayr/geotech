"""Export world geometries as per-tile pre-annotations in CVAT for images 1.1 format."""
import os
from xml.sax.saxutils import quoteattr

import numpy as np
import cv2
from shapely.geometry import box
from shapely.strtree import STRtree

from .common import TILE_PX, TILE_RES, exg

GAP_DISRUPT_M = 5.0
COVER_RES = 0.1
VEG_EXG = 0.10


def tile_cover(rgb, poly_px):
    """interrow_cover for one clipped inter-row polygon (rules 5.2), judged within this tile."""
    m = np.zeros(rgb.shape[:2], np.uint8)
    cv2.fillPoly(m, [poly_px.astype(np.int32)], 1)
    m = m.astype(bool) & (rgb.max(2) > 0)
    if m.sum() < 20:
        return None
    e = exg(rgb)[m]
    br = rgb.astype(np.float32).mean(2)[m]
    veg = (e > VEG_EXG).mean()
    dark = ((br < 35) & (e <= VEG_EXG)).mean()
    if dark > 0.5:
        return "unassessable"
    return "bare_soil" if veg < 0.25 else ("vegetation" if veg > 0.75 else "mixed")

LABELS = {
    "vineyard": ("polygon", ["vineyard_id"]),
    "waste": ("rectangle", ["vineyard_id"]),
    "row": ("polyline", ["vineyard_id", "row_id", "row_structure"]),
    "interrow_area": ("polygon", ["vineyard_id", "interrow_cover"]),
}
ATTR_VALUES = {
    "row_structure": ["regular", "disrupted", "unassessable"],
    "interrow_cover": ["bare_soil", "vegetation", "mixed", "unassessable"],
}


def _parts(g, kind):
    t = g.geom_type
    if t.startswith("Multi") or t == "GeometryCollection":
        return [p for p in g.geoms if p.geom_type == kind]
    return [g] if t == kind else []


def _attrs(names, props):
    return "".join(f'\n      <attribute name="{n}">{props.get(n, "")}</attribute>' for n in names)


def write_cvat(ti, layers, path, row_gaps=None):
    row_gaps = row_gaps or {}
    idx = {}
    for lab in LABELS:
        g = layers.get(lab)
        if g is None or len(g) == 0:
            continue
        recs = g.to_dict("records")
        idx[lab] = (STRtree([r["geometry"] for r in recs]), recs)

    meta = ['<meta><task><name>siret3</name><labels>']
    for lab, (shape, attrs) in LABELS.items():
        meta.append(f"<label><name>{lab}</name><type>{shape}</type><attributes>")
        for a in attrs:
            if a in ATTR_VALUES:
                vals = "\n".join(ATTR_VALUES[a])
                meta.append(f"<attribute><name>{a}</name><mutable>False</mutable><input_type>select</input_type>"
                            f"<default_value>{ATTR_VALUES[a][0]}</default_value><values>{vals}</values></attribute>")
            else:
                meta.append(f"<attribute><name>{a}</name><mutable>False</mutable><input_type>text</input_type>"
                            f"<default_value></default_value><values></values></attribute>")
        meta.append("</attributes></label>")
    meta.append("</labels></task></meta>")

    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<annotations>", "  <version>1.1</version>", "  " + "".join(meta)]
    for iid, key in enumerate(sorted(ti.tiles)):
        f, b = ti.tiles[key]
        name = os.path.basename(f)
        tb = box(b.left, b.bottom, b.right, b.top)

        def px(x, y):
            return (x - b.left) / TILE_RES, (b.top - y) / TILE_RES

        def pts(coords):
            out = []
            for x, y in coords:
                u, v = px(x, y)
                out.append(f"{min(max(u, 0), TILE_PX):.2f},{min(max(v, 0), TILE_PX):.2f}")
            return ";".join(out)

        body = []
        tile_rgb = None
        for lab, (shape, attrs) in LABELS.items():
            if lab not in idx:
                continue
            tree, recs = idx[lab]
            for j in tree.query(tb):
                r = recs[j]
                g = r["geometry"].intersection(tb)
                if g.is_empty:
                    continue
                if shape == "polygon":
                    for p in _parts(g, "Polygon"):
                        p = p.simplify(0.02)
                        if p.area < 0.01 or len(p.exterior.coords) < 4:
                            continue
                        if lab == "interrow_area":
                            if tile_rgb is None:
                                import rasterio
                                from rasterio.enums import Resampling
                                n = int(TILE_PX * TILE_RES / COVER_RES)
                                with rasterio.open(f) as d:
                                    tile_rgb = d.read(out_shape=(3, n, n), resampling=Resampling.average).transpose(1, 2, 0)
                            ext = np.asarray(p.exterior.coords)
                            pp = np.c_[(ext[:, 0] - b.left) / COVER_RES, (b.top - ext[:, 1]) / COVER_RES]
                            cv_ = tile_cover(tile_rgb, pp)
                            r = dict(r, interrow_cover=cv_ or r.get("interrow_cover"))
                        body.append(f'    <polygon label="{lab}" source="auto" occluded="0" z_order="0" '
                                    f'points="{pts(list(p.exterior.coords)[:-1])}">{_attrs(attrs, r)}\n    </polygon>')
                elif shape == "polyline":
                    for l in _parts(g, "LineString"):
                        if l.length < 0.2:
                            continue
                        if r.get("row_structure") != "unassessable":
                            gl = [x.intersection(tb).length for x in row_gaps.get(r["row_id"], [])]
                            r = dict(r, row_structure="disrupted" if any(v >= GAP_DISRUPT_M for v in gl) else "regular")
                        body.append(f'    <polyline label="{lab}" source="auto" occluded="0" z_order="0" '
                                    f'points="{pts(l.coords)}">{_attrs(attrs, r)}\n    </polyline>')
                else:
                    x0, y0, x1, y1 = g.bounds
                    u0, v1 = px(x0, y0)
                    u1, v0 = px(x1, y1)
                    if u1 - u0 < 1 or v1 - v0 < 1:
                        continue
                    body.append(f'    <box label="{lab}" source="auto" occluded="0" z_order="0" '
                                f'xtl="{u0:.2f}" ytl="{v0:.2f}" xbr="{u1:.2f}" ybr="{v1:.2f}">{_attrs(attrs, r)}\n    </box>')
        lines.append(f'  <image id="{iid}" name={quoteattr(name)} width="{TILE_PX}" height="{TILE_PX}">')
        lines.extend(body)
        lines.append("  </image>")
    lines.append("</annotations>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
