"""Refine plant outlines with SAM (Segment Anything, Meta; pretrained, zero-shot - no training here).

Each plant polygon found by the classical CIVE step becomes a prompt (its box + a point on it); SAM
returns a precise outline. The SAM outline replaces the classical one only when it is plausible
(similar size, overlapping it, staying on the row band); otherwise the classical polygon is kept.
Weights: model/sam_vit_b_01ec64.pth (https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth)
"""
import time

import cv2
import numpy as np
import torch
from shapely.geometry import Polygon, box

from .common import TILE_M

RES = 0.05
MARGIN_M = 2.0
BOX_PAD_M = 0.10
MIN_RATIO, MAX_RATIO, MIN_OVERLAP = 0.5, 2.2, 0.3
_PRED = None


def predictor(ckpt):
    global _PRED
    if _PRED is None:
        from segment_anything import sam_model_registry, SamPredictor
        torch.set_num_threads(max(1, torch.get_num_threads()))
        sam = sam_model_registry["vit_b"](checkpoint=ckpt)
        sam.eval()
        _PRED = SamPredictor(sam)
    return _PRED


def _mask_polygon(m, gx, gy, res):
    cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
    if len(c) < 3:
        return None
    p = Polygon(np.c_[gx + (c[:, 0] + 0.5) * res, gy - (c[:, 1] + 0.5) * res]).buffer(0)
    if p.geom_type != "Polygon":
        p = max(getattr(p, "geoms", [p]), key=lambda q: q.area)
    return p.simplify(0.02) if not p.is_empty else None


@torch.no_grad()
def refine_with_sam(blocks, ti, ckpt, log=print, batch=64):
    pred = predictor(ckpt)
    rows = [r for b in blocks for r in b["rows"]]
    items = [(ri, k, v) for ri, r in enumerate(rows) for k, v in enumerate(r["vines"])]
    if not items:
        return 0
    cents = np.array([[v.centroid.x, v.centroid.y] for _, _, v in items])
    t0, n_ok, n_tiles = time.time(), 0, 0
    for key, (_, bnd) in sorted(ti.tiles.items()):
        sel = np.nonzero((cents[:, 0] >= bnd.left) & (cents[:, 0] < bnd.right) &
                         (cents[:, 1] > bnd.bottom) & (cents[:, 1] <= bnd.top))[0]
        if len(sel) == 0:
            continue
        L, T = bnd.left - MARGIN_M, bnd.top + MARGIN_M
        rgb, valid, tr = ti.read(L, T, TILE_M + 2 * MARGIN_M, TILE_M + 2 * MARGIN_M, RES)
        pred.set_image(rgb)
        n_tiles += 1
        gx, gy, res = tr
        boxes, pts = [], []
        for i in sel:
            _, _, v = items[i]
            x0, y0, x1, y1 = v.bounds
            boxes.append([(x0 - BOX_PAD_M - gx) / res, (gy - y1 - BOX_PAD_M) / res,
                          (x1 + BOX_PAD_M - gx) / res, (gy - y0 + BOX_PAD_M) / res])
            rp = v.representative_point()
            pts.append([[(rp.x - gx) / res, (gy - rp.y) / res]])
        H, W = rgb.shape[:2]
        for s in range(0, len(sel), batch):
            bx = torch.tensor(boxes[s:s + batch], dtype=torch.float32)
            pt = torch.tensor(pts[s:s + batch], dtype=torch.float32)
            bx_t = pred.transform.apply_boxes_torch(bx, (H, W))
            pt_t = pred.transform.apply_coords_torch(pt, (H, W))
            lb = torch.ones(pt_t.shape[:2], dtype=torch.int64)
            masks, scores, _ = pred.predict_torch(point_coords=pt_t, point_labels=lb, boxes=bx_t,
                                                  multimask_output=False)
            for j, i in enumerate(sel[s:s + batch]):
                ri, k, v = items[i]
                p = _mask_polygon(masks[j, 0].numpy() & valid, gx, gy, res)
                if p is None:
                    continue
                ratio = p.area / max(v.area, 1e-6)
                ov = p.intersection(v).area / max(min(p.area, v.area), 1e-6)
                if MIN_RATIO <= ratio <= MAX_RATIO and ov >= MIN_OVERLAP and \
                        p.distance(rows[ri]["geometry"]) < 0.05 and p.area >= 0.05:
                    rows[ri]["vines"][k] = p
                    n_ok += 1
    log(f"SAM: refined {n_ok}/{len(items)} plant outlines on {n_tiles} tiles in {time.time() - t0:.0f} s")
    return n_ok
