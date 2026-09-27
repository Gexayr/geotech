"""Waste detection (classical): small objects whose colour is anomalous for a vineyard scene.

Two cues at 5 cm/px inside the vineyard blocks + a surrounding zone:
  * bright   - much brighter than the local background and unsaturated (plastic film, bags, paper,
               styrofoam, white containers)
  * vivid    - strongly saturated non-vegetation, non-soil hues (blue / purple / red / pink plastics)
Blobs are size-filtered (0.02 .. 6 m2), clustered (items < 0.6 m apart form one object), and bright
blobs lying on a row axis are rejected (white trunk guards / posts are part of the planting).
"""
import numpy as np
import cv2
from scipy import ndimage as ndi
from shapely.geometry import box, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .common import TILE_M

RES = 0.05


def detect_waste(ti, zone, row_lines, log=print, exclude=None):
    """zone: shapely (Multi)Polygon to search; row_lines: list of LineStrings (row axes)."""
    tree = STRtree(row_lines) if row_lines else None
    if exclude is not None:
        zone = zone.difference(exclude)
    out = []
    keys = sorted(ti.tiles)
    for (r, c) in keys:
        f, b = ti.tiles[(r, c)]
        tb = box(b.left, b.bottom, b.right, b.top)
        if not tb.intersects(zone):
            continue
        rgb, valid, tr = ti.read(b.left, b.top, TILE_M, TILE_M, RES)
        H, W = valid.shape
        zm = np.zeros((H, W), np.uint8)
        zi = zone.intersection(tb)
        for g in (zi.geoms if hasattr(zi, "geoms") else [zi]):
            if g.is_empty or g.geom_type != "Polygon":
                continue
            ext = np.asarray(g.exterior.coords)
            cv2.fillPoly(zm, [np.c_[(ext[:, 0] - tr[0]) / RES, (tr[1] - ext[:, 1]) / RES].astype(np.int32)], 1)
            for hole in g.interiors:
                h = np.asarray(hole.coords)
                cv2.fillPoly(zm, [np.c_[(h[:, 0] - tr[0]) / RES, (tr[1] - h[:, 1]) / RES].astype(np.int32)], 0)
        zm = zm.astype(bool) & valid
        if zm.sum() < 100:
            continue
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        Hh, S, V = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
        gray = rgb.astype(np.float32).mean(2)
        bg = cv2.medianBlur(rgb, 41).astype(np.float32).mean(2)
        bright = (gray - bg > 70) & (V > 225) & (S < 35)
        vivid = (S > 140) & (V > 110) & ((Hh >= 100) & (Hh <= 170))           # blue .. purple .. pink
        vivid |= (S > 190) & (V > 140) & ((Hh <= 3) | (Hh >= 174))            # saturated red
        edge = cv2.dilate((~valid).astype(np.uint8), np.ones((41, 41), np.uint8)).astype(bool)
        bright &= ~edge
        vivid &= ~edge
        m = (bright | vivid) & zm
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))   # ~0.6 m clustering
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            core = (lab[y:y + h, x:x + w] == i) & ((bright | vivid)[y:y + h, x:x + w])
            area = core.sum() * RES ** 2
            if area < 0.06 or area > 4:
                continue
            ys, xs = np.nonzero(core)
            x0, x1 = x + xs.min(), x + xs.max() + 1
            y0, y1 = y + ys.min(), y + ys.max() + 1
            wx0, wx1 = tr[0] + x0 * RES, tr[0] + x1 * RES
            wy1, wy0 = tr[1] - y0 * RES, tr[1] - y1 * RES
            bw, bh = wx1 - wx0, wy1 - wy0
            if max(bw, bh) > 3 or max(bw, bh) / max(min(bw, bh), 0.05) > 4:   # long thin = hose / tube / pipe
                continue
            if area / max(bw * bh, 1e-6) < 0.25:                                # scattered speckle, not an object
                continue
            is_bright = bright[y:y + h, x:x + w][core].mean() > 0.5
            ctr = Point((wx0 + wx1) / 2, (wy0 + wy1) / 2)
            if is_bright and tree is not None:
                near = tree.query(ctr.buffer(0.6))
                if len(near):
                    continue
            out.append(dict(geometry=box(wx0, wy0, wx1, wy1), kind="bright" if is_bright else "vivid",
                            area_px_m2=round(float(area), 3), tile=f"r{r:03d}_c{c:03d}"))
    # merge boxes that touch across tile borders
    merged = []
    if out:
        geoms = [o["geometry"] for o in out]
        u = unary_union([g.buffer(0.3) for g in geoms])
        for part in (u.geoms if hasattr(u, "geoms") else [u]):
            members = [o for o in out if o["geometry"].intersects(part)]
            bx = unary_union([o["geometry"] for o in members]).bounds
            merged.append(dict(geometry=box(*bx), kind=members[0]["kind"],
                               area_px_m2=sum(o["area_px_m2"] for o in members)))
    log(f"waste candidates: {len(merged)}")
    return merged
