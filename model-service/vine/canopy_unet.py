"""Replace the rule-based plant polygons with U-Net instance segmentation.

For every tile that touches a vineyard block the U-Net is run on the tile plus a 3 m margin (context
at tile edges); plants whose centre lies in the tile core are kept, each is assigned to the nearest
detected row axis (<= 0.7 m, same block) and inherits its vineyard_id / row_id. Plants that are not
on a vine row (weeds, trees, other crops) are dropped.
"""
import time

import cv2
import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import unet as U
from .common import TILE_M

MARGIN_M = 3.0
ROW_DIST_M = 0.7


def _polys(lab, gx, gy, res):
    out = []
    for k in np.unique(lab):
        if k == 0:
            continue
        m = (lab == k).astype(np.uint8)
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
        if len(c) < 3:
            continue
        p = Polygon(np.c_[gx + c[:, 0] * res, gy - c[:, 1] * res]).buffer(0)
        if p.geom_type != "Polygon":
            p = max(getattr(p, "geoms", [p]), key=lambda q: q.area)
        if not p.is_empty:
            out.append(p.simplify(0.03))
    return out


def apply_unet(blocks, ti, weights, log=print):
    net = U.load(weights)
    rows = [r for b in blocks for r in b["rows"]]
    if not rows:
        return 0
    tree = STRtree([r["geometry"] for r in rows])
    zone = unary_union([b["geometry"] for b in blocks]).buffer(1.0)
    for r in rows:
        r["vines"] = []
    t0, n_tiles, n_plants = time.time(), 0, 0
    for key, (_, bnd) in sorted(ti.tiles.items()):
        core = box(bnd.left, bnd.bottom, bnd.right, bnd.top)
        if not core.intersects(zone):
            continue
        L, T = bnd.left - MARGIN_M, bnd.top + MARGIN_M
        rgb, valid, tr = ti.read(L, T, TILE_M + 2 * MARGIN_M, TILE_M + 2 * MARGIN_M, U.RES)
        if valid.mean() < 0.01:
            continue
        prob = U.predict(net, rgb)
        prob[~valid] = [1, 0, 0]
        lab = U.instances(prob)
        n_tiles += 1
        for p in _polys(lab, tr[0], tr[1], tr[2]):
            c = p.centroid
            if not core.contains(c):
                continue                                   # the neighbouring tile owns this plant
            best = None
            for j in tree.query(c.buffer(ROW_DIST_M)):
                d = rows[j]["geometry"].distance(c)
                if d <= ROW_DIST_M and (best is None or d < best[0]):
                    best = (d, j)
            if best is None:
                continue
            rows[best[1]]["vines"].append(p)
            n_plants += 1
    log(f"U-Net canopy: {n_plants} plants on {n_tiles} tiles in {time.time() - t0:.0f} s")
    return n_plants
