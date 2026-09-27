"""Stage 2 - fine per-region analysis at 5 cm/px (classical CV).

For one candidate region:
  1. vegetation mask from excess-green (Otsu), large tree crowns removed by morphological opening
  2. exact row angle + row spacing from projection histograms of vegetation pixels
  3. rotate so rows are horizontal; in 5 m chunks find row peaks of the across-row profile and
     link them into row tracks (tracks survive gaps, so planting gaps do not split a row)
  4. blocks = connected components of a smoothed canopy-density map (roads / headlands break it)
  5. per row: extent, individual vine segments (split at occupancy minima), gaps, structure class
  6. inter-row strips between adjacent canopy edges, cover class from vegetation fraction
Everything is returned in world coordinates (EPSG:32635).
"""
import os

import numpy as np
import cv2
from scipy import ndimage as ndi
from scipy.signal import find_peaks
from shapely.geometry import Polygon, LineString, Point, MultiPolygon
from shapely.ops import unary_union

from .common import exg

_CLF = None
CLF_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model", "canopy_clf.joblib")
CLF_THRESHOLD = 0.5


MIN_CONTOUR_M2 = 0.04       # canopy fragments smaller than this are noise
MIN_PIECE_M2 = 0.08         # plants smaller than this are dropped
JOIN_BUF_M = 0.12           # fragments of one plant closer than 2x this are joined into one polygon
CLOSE_K = 5                 # morphological closing (px at 5 cm) of the canopy mask
ERODE_PX = 0                # optional erosion of each plant mask (px)
ROW_PERIOD = (1.8, 4.2)     # search range (m) for the exact row spacing of a region
ANGLE_SEARCH_DEG = 10.0      # refine the region row angle within +- this
LATTICE = True               # merge collinear row pieces + fill rows missing from the parallel-row lattice
LATTICE_MAX_GAP_M = 40.0    # max gap bridged when merging two pieces of one row
CANOPY_MODE = "cive"         # "cive" (training-free, default) | "auto" (trained classifier if present, else ExG rule) | "grabcut" | "rule"


def _grabcut_row(rrgb, TREE, VAL, X, Y, xa, xb_, y0, y1, hb, P):
    """Self-calibrating canopy mask for one row (rotated frame, row horizontal).

    GrabCut = colour Gaussian mixtures (fg / bg) learned from THIS row + graph-cut boundary refinement.
    Seeds come from row geometry: strong green on the row axis = plant, the middle of the inter-row,
    non-green or tree / no-data pixels = ground.  Works for any row direction and soil colour.
    """
    Hr = rrgb.shape[0]
    ext = int(0.5 * P)
    Y0, Y1 = max(y0 - ext, 0), min(y1 + ext, Hr)
    img = np.ascontiguousarray(rrgb[Y0:Y1, xa:xb_])
    if img.size == 0:
        return np.zeros((y1 - y0, xb_ - xa), np.uint8)
    dy = np.abs(np.arange(Y0, Y1)[:, None] - Y[None, :xb_ - xa])
    x = img.astype(np.float32)
    e = (2 * x[..., 1] - x[..., 0] - x[..., 2]) / (x.sum(2) + 1)
    bright = x.sum(2)
    inband = dy <= hb
    ev = e[inband & (bright > 60)]
    t_hi = float(np.clip(_otsu(ev), 0.04, 0.2)) if ev.size > 50 else 0.08
    m = np.full(img.shape[:2], cv2.GC_PR_BGD, np.uint8)
    m[inband & (e > 0.6 * t_hi) & (bright > 60)] = cv2.GC_PR_FGD
    m[(dy <= 0.35 / RES) & (e > t_hi) & (bright > 90)] = cv2.GC_FGD
    m[dy >= 0.42 * P] = cv2.GC_BGD
    m[(e < 0.0) & (dy > 0.2 / RES)] = cv2.GC_BGD
    # shadows: darker than the local ground and not green -> ground
    vloc = cv2.blur(bright, (41, 41))
    shadow = (bright < 0.75 * vloc) & (e < 0.5 * t_hi)
    m[shadow] = cv2.GC_BGD
    m[TREE[Y0:Y1, xa:xb_] | ~VAL[Y0:Y1, xa:xb_]] = cv2.GC_BGD
    if not (m == cv2.GC_FGD).any() or not (m == cv2.GC_BGD).any():
        return np.zeros((y1 - y0, xb_ - xa), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(img, m, None, bgd, fgd, GRABCUT_ITERS, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return np.zeros((y1 - y0, xb_ - xa), np.uint8)
    fg = (((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD)) & (e > GC_MIN_GREEN * t_hi)).astype(np.uint8)
    return fg[y0 - Y0:y0 - Y0 + (y1 - y0)]


GRABCUT_ITERS = 3
CIVE_ROW_WEIGHT = 0.0        # 0 = one Otsu threshold per region, 1 = per row


def _cive(rgb):
    """Negated Colour Index of Vegetation Extraction (Kataoka 2003), scaled: higher = more vegetation."""
    x = rgb.astype(np.float32)
    return -(0.441 * x[..., 0] - 0.811 * x[..., 1] + 0.385 * x[..., 2] + 18.787) / 255.0
GC_MIN_GREEN = 0.4           # final canopy pixels must be at least this fraction of the row's green threshold


def canopy_classifier():
    """Trained canopy pixel classifier (tools/train_canopy.py); None -> fall back to the ExG rule."""
    global _CLF
    if _CLF is None and os.path.exists(CLF_PATH):
        import joblib
        _CLF = joblib.load(CLF_PATH)
    return _CLF

RES = 0.05
CHUNK_M = 5.0
GAP_INSPECT_M = 5.0        # gaps >= this inside a row: row 'disrupted' + inspection target (rules 4.3)
TREE_OPEN_M = 2.0          # vegetation blobs wider than this are tree crowns / bushes, not vine rows

B_VEG, B_TREE, B_VALID, B_REG, B_DARK, B_LO = 1, 2, 4, 8, 16, 32
T_CANOPY_BAND = 0.08        # lower ExG threshold used only inside a row's canopy band
MIN_BRIGHT = 120            # R+G+B floor: dark shadow pixels have unreliable chromaticity
PLANT_JOIN_M = 0.8          # canopy pieces closer than this along the row belong to one plant
CUT_GAP_M = 2.5            # min gap length in each row for a track crossing the block
CUT_MIN_ROWS = 5           # a cut line must cross at least this many adjacent rows
PLANT_MIN_M = 0.25          # shorter pieces are weeds / noise
PLANT_SPLIT = "profile"     # 'watershed' (split at canopy necks) or 'profile' (1-D occupancy minima)
PLANT_SPLIT_LEN = 1.8       # canopy runs longer than this (m) may hold several plants
PLANT_MIN_SEP = 0.9         # minimum distance between two plant centres (m)
PLANT_SP_MIN = 1.0          # lower bound for the planting distance used to split continuous canopy


# ----------------------------------------------------------------------------- helpers
def _poly_to_px(poly, tr):
    gx, gy, res = tr
    out = []
    geoms = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
    for g in geoms:
        c = np.asarray(g.exterior.coords)
        out.append(np.c_[(c[:, 0] - gx) / res, (gy - c[:, 1]) / res].astype(np.int32))
    return out


def _otsu(v):
    v = np.clip(v, -0.2, 0.6)
    h, e = np.histogram(v, 256, (-0.2, 0.6))
    h = h.astype(float)
    w = np.cumsum(h)
    mu = np.cumsum(h * (e[:-1] + e[1:]) / 2)
    wt, mt = w[-1], mu[-1]
    var = (mt * w - mu * wt) ** 2 / (w * (wt - w) + 1e-9)
    return (e[:-1] + e[1:])[np.nanargmax(var)] / 2


def _period_from_profile(p, res, lo, hi):
    p = p - p.mean()
    ac = np.correlate(p, p, "full")[len(p) - 1:]
    a, b = int(lo / res), min(int(hi / res), len(ac) - 1)
    if b <= a + 2:
        return None, 0
    k = a + int(np.argmax(ac[a:b]))
    return k * res, ac[k] / (ac[0] + 1e-9)


# ----------------------------------------------------------------------------- main
def process_region(ti, geom, freq_angle, log=print, pad=8.0):
    region = geom.buffer(pad)
    L, B, R, T = region.bounds
    rgb, valid, tr = ti.read(L, T, R - L, T - B, RES)
    H, W = valid.shape
    reg = np.zeros((H, W), np.uint8)
    cv2.fillPoly(reg, _poly_to_px(region, tr), 1)
    reg = reg.astype(bool) & valid

    e = exg(rgb)
    t_veg = float(np.clip(_otsu(e[reg][::7]), 0.03, 0.15))
    veg = (e > t_veg) & valid
    vlo = (e > T_CANOPY_BAND) & valid & (rgb.astype(np.uint16).sum(2) > MIN_BRIGHT)
    bright = rgb.astype(np.uint16).sum(2)
    dark = (bright < 90) & valid
    del e

    # tree crowns: vegetation that survives an opening wider than any vine row (at 0.2 m for speed)
    f = 4
    vs = cv2.resize(veg.astype(np.float32), (W // f, H // f), interpolation=cv2.INTER_AREA) > 0.5
    k = int(round(TREE_OPEN_M / (RES * f))) | 1
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    tree_s = cv2.morphologyEx(vs.astype(np.uint8), cv2.MORPH_OPEN, ker)
    tree_s = cv2.dilate(tree_s, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    tree = cv2.resize(tree_s, (W, H), interpolation=cv2.INTER_NEAREST).astype(bool)
    canopy = veg & ~tree

    # ---- refine angle + period with projection histograms of canopy pixels
    ys, xs = np.nonzero(canopy & reg)
    if len(xs) < 2000:
        return None
    sel = np.random.default_rng(0).choice(len(xs), min(400000, len(xs)), replace=False)
    xs_, ys_ = xs[sel] * RES, ys[sel] * RES
    best = None
    for da in np.deg2rad(np.arange(-ANGLE_SEARCH_DEG, ANGLE_SEARCH_DEG + 0.01, 0.25)):
        a = freq_angle + da
        v = xs_ * np.cos(a) + ys_ * np.sin(a)
        h, _ = np.histogram(v, bins=np.arange(v.min(), v.max() + RES, RES))
        h = ndi.gaussian_filter1d(h.astype(float), 1.5)
        hp = h - ndi.gaussian_filter1d(h, 40)
        sc = (hp ** 2).sum() / (h ** 2).sum()
        if best is None or sc > best[0]:
            best = (sc, a, h)
    _, a, hbest = best
    period, pq = _period_from_profile(hbest - ndi.gaussian_filter1d(hbest, 40), RES, *ROW_PERIOD)
    if period is None or pq < 0.15:
        log(f"   no clear row period (q={pq:.2f}) - skipped")
        return None

    # ---- rotate: X' along rows, Y' across rows
    ca, sa = np.cos(a), np.sin(a)
    corners = np.array([[0, 0], [W, 0], [0, H], [W, H]], float)
    u = -corners[:, 0] * sa + corners[:, 1] * ca
    v = corners[:, 0] * ca + corners[:, 1] * sa
    M = np.array([[-sa, ca, -u.min()], [ca, sa, -v.min()]])
    Wr, Hr = int(np.ceil(u.max() - u.min())) + 1, int(np.ceil(v.max() - v.min())) + 1
    Minv = cv2.invertAffineTransform(M)
    bits = (canopy * B_VEG | tree * B_TREE | valid * B_VALID | reg * B_REG | dark * B_DARK
            | (vlo & ~tree) * B_LO).astype(np.uint8)
    del vlo
    vegall = veg
    del canopy, tree, dark
    rb = cv2.warpAffine(bits, M, (Wr, Hr), flags=cv2.INTER_NEAREST, borderValue=0)
    rveg = cv2.warpAffine(vegall.astype(np.uint8), M, (Wr, Hr), flags=cv2.INTER_NEAREST, borderValue=0)
    clf = canopy_classifier()
    need_rgb = clf is not None or CANOPY_MODE in ("grabcut", "cive")
    rrgb = cv2.warpAffine(rgb, M, (Wr, Hr), flags=cv2.INTER_LINEAR, borderValue=0) if need_rgb else None
    del rgb
    del bits, vegall
    C = ((rb & B_VEG) > 0)
    REG = (rb & B_REG) > 0
    TREE = (rb & B_TREE) > 0
    VAL = (rb & B_VALID) > 0
    DARK = (rb & B_DARK) > 0
    LO = (rb & B_LO) > 0

    def rot_to_world(X, Y):
        X, Y = np.asarray(X, float), np.asarray(Y, float)
        px = Minv[0, 0] * X + Minv[0, 1] * Y + Minv[0, 2]
        py = Minv[1, 0] * X + Minv[1, 1] * Y + Minv[1, 2]
        return tr[0] + px * RES, tr[1] - py * RES

    P = period / RES                          # row period in px
    cw = int(CHUNK_M / RES)
    Cf = C.astype(np.float32)
    cols = np.nonzero(REG.any(0))[0]
    x_lo, x_hi = cols.min(), cols.max()

    # ---- chunk peaks + tracking
    tracks = []                              # each: list of (xc, y)
    active = []
    nchunk = 0
    for x0 in range(x_lo, x_hi, cw):
        x1 = min(x0 + cw, x_hi)
        sub = Cf[:, x0:x1]
        regc = REG[:, x0:x1].sum(1)
        prof = sub.sum(1) / np.maximum(regc, 1)
        prof[regc < 0.5 * (x1 - x0)] = 0
        prof = ndi.gaussian_filter1d(prof, 0.12 / RES)
        if prof.max() < 0.05:
            nchunk += 1
            continue
        pk, pr = find_peaks(prof, distance=0.6 * P, prominence=0.04, height=0.06)
        xc = (x0 + x1) / 2
        # sub-pixel refine
        ypk = []
        for p_ in pk:
            if 0 < p_ < len(prof) - 1:
                d = prof[p_ - 1] - 2 * prof[p_] + prof[p_ + 1]
                ypk.append(p_ + (0.5 * (prof[p_ - 1] - prof[p_ + 1]) / d if d < 0 else 0))
            else:
                ypk.append(float(p_))
        used = set()
        cand = []
        for ti_, t in enumerate(active):
            if nchunk - t["last"] > 8:            # lost for > 40 m
                continue
            pts = t["pts"]
            if len(pts) >= 3:
                (xa, ya), (xb, yb) = pts[-3], pts[-1]
                pred = yb + (yb - ya) / (xb - xa) * (xc - xb)
            else:
                pred = pts[-1][1]
            for j, y in enumerate(ypk):
                d = abs(y - pred)
                if d < 0.3 * P:
                    cand.append((d, ti_, j))
        cand.sort()
        tused = set()
        for d, ti_, j in cand:
            if ti_ in tused or j in used:
                continue
            tused.add(ti_)
            used.add(j)
            active[ti_]["pts"].append((xc, ypk[j]))
            active[ti_]["last"] = nchunk
        for j, y in enumerate(ypk):
            if j not in used:
                t = {"pts": [(xc, y)], "last": nchunk}
                active.append(t)
                tracks.append(t)
        nchunk += 1
    tracks = [t["pts"] for t in tracks if len(t["pts"]) >= 2]
    if not tracks:
        return None

    # ---- per-track profiles: canopy on the axis vs. canopy half-way to the next row (row contrast).
    # Grass or weeds are green everywhere -> contrast ~0, vine rows -> clearly positive.
    hw_band = min(0.35 * P, 0.7 / RES)
    hb = int(hw_band)
    ha = max(int(0.2 * P), int(0.25 / RES))
    hm = max(int(0.08 * P), 2)
    sig = 1.5 / RES
    prof = []
    for pts in tracks:
        pts = np.array(pts)
        pts[:, 1] = ndi.median_filter(pts[:, 1], 3, mode="nearest")
        xa, xb = max(int(pts[0, 0] - cw / 2), 0), min(int(pts[-1, 0] + cw / 2), Wr - 1)
        X = np.arange(xa, xb)
        Yax = np.interp(X, pts[:, 0], pts[:, 1])
        Yi = np.clip(np.round(Yax).astype(int), 0, Hr - 1)
        occ = np.zeros(len(X)); trf = np.zeros(len(X)); drk = np.zeros(len(X))
        for dy in range(-hb, hb + 1):
            yy = np.clip(Yi + dy, 0, Hr - 1)
            occ += C[yy, X]; trf += TREE[yy, X]; drk += DARK[yy, X] & ~C[yy, X]
        occ /= 2 * hb + 1; trf /= 2 * hb + 1; drk /= 2 * hb + 1
        oa = np.zeros(len(X))
        for dy in range(-ha, ha + 1):
            oa += C[np.clip(Yi + dy, 0, Hr - 1), X]
        oa /= 2 * ha + 1
        mid = np.zeros(len(X)); nm = 0
        for s_ in (-1, 1):
            for dy in range(-hm, hm + 1):
                mid += C[np.clip(Yi + int(s_ * P / 2) + dy, 0, Hr - 1), X]; nm += 1
        mid /= nm
        con = ndi.gaussian_filter1d(oa, sig) - ndi.gaussian_filter1d(mid, sig)
        pres = (con > 0.05) & (ndi.gaussian_filter1d(oa, sig) > 0.06) & REG[Yi, X]
        pres = ndi.binary_closing(pres, np.ones(int(3 / RES)))
        prof.append(dict(X=X, Y=Yax, Yi=Yi, occ=occ, tree=trf, dark=drk, pres=pres))

    # ---- block map: rasterise row support, join adjacent rows across (vertical closing),
    # so single-row planting gaps are bridged but roads / headlands crossing all rows split blocks
    f2 = 4
    hs, ws = Hr // f2 + 1, Wr // f2 + 1
    RP = np.zeros((hs, ws), bool)
    half = max(1, int(0.5 * P / f2))
    for p_ in prof:
        k = p_["pres"]
        xs_s, ys_s = p_["X"][k] // f2, p_["Yi"][k] // f2
        for dy in range(-half, half + 1):
            RP[np.clip(ys_s + dy, 0, hs - 1), xs_s] = True
    vk = np.ones((int(1.3 * P / f2) | 1, 1), bool)
    BM = ndi.binary_closing(RP, vk)
    BM = ndi.binary_opening(BM, np.ones((1, max(3, int(2 / (RES * f2)))), bool))
    BM = ndi.binary_fill_holes(BM)
    blab, nb = ndi.label(BM)
    bsz = ndi.sum(BM, blab, range(1, nb + 1)) * (RES * f2) ** 2
    for i, s in enumerate(bsz):
        if s < 150:
            blab[blab == i + 1] = 0

    rows = []
    for p_ in prof:
        X, Yi = p_["X"], p_["Yi"]
        comp = blab[np.clip(Yi // f2, 0, hs - 1), np.clip(X // f2, 0, ws - 1)]
        comp = np.where(p_["pres"], comp, 0)
        for cid in np.unique(comp[comp > 0]):
            idx = np.nonzero(comp == cid)[0]
            if (idx[-1] - idx[0]) * RES < 2.0:
                continue
            s0, s1 = idx[0], idx[-1]
            # tighten ends to actual canopy
            o = ndi.gaussian_filter1d(p_["occ"][s0:s1 + 1], 0.1 / RES) > 0.08
            if not o.any():
                continue
            oi = np.nonzero(o)[0]
            s0, s1 = s0 + oi[0], s0 + oi[-1]
            rows.append(dict(block=int(cid), X=X[s0:s1 + 1], Y=p_["Y"][s0:s1 + 1], occ=p_["occ"][s0:s1 + 1],
                             tree=p_["tree"][s0:s1 + 1], dark=p_["dark"][s0:s1 + 1]))
    if not rows:
        return None

    # drop near-duplicate rows in the same block (two tracks on one physical row)
    rows.sort(key=lambda r: (r["block"], np.median(r["Y"])))
    keep = []
    for r in rows:
        dup = False
        for qi in range(max(0, len(keep) - 4), len(keep)):
            q = keep[qi]
            if q["block"] != r["block"]:
                continue
            lo, hi = max(q["X"][0], r["X"][0]), min(q["X"][-1], r["X"][-1])
            if hi - lo > 0.3 * min(len(q["X"]), len(r["X"])):
                yq = np.interp(np.arange(lo, hi), q["X"], q["Y"])
                yr = np.interp(np.arange(lo, hi), r["X"], r["Y"])
                if np.median(np.abs(yq - yr)) < 0.45 * P:
                    dup = True
                    if len(r["X"]) > len(q["X"]):
                        keep[qi] = r
                    break
        if not dup:
            keep.append(r)
    rows = keep

    # ---- tracks / roads crossing the block: gaps aligned across many adjacent rows form a cut line.
    # Rules 6: "a road or a track always separates blocks" -> split rows and blocks there.
    gap_segs = []
    for ri_, r in enumerate(rows):
        pres_ = ndi.binary_closing(ndi.gaussian_filter1d(r["occ"], 0.1 / RES) > 0.08, np.ones(int(0.25 / RES)))
        lab_, _ = ndi.label(~pres_)
        for sl in ndi.find_objects(lab_):
            g0, g1 = sl[0].start, sl[0].stop
            if g0 > 0 and g1 < len(pres_) and (g1 - g0) * RES >= CUT_GAP_M:
                gap_segs.append((ri_, g0, g1))
    if gap_segs:
        GR = np.zeros((hs, ws), bool)
        halfg = max(1, int(0.6 * P / f2))
        for ri_, g0, g1 in gap_segs:
            r = rows[ri_]
            xs_s = r["X"][g0:g1] // f2
            ys_s = np.round(r["Y"][g0:g1]).astype(int) // f2
            for dy in range(-halfg, halfg + 1):
                GR[np.clip(ys_s + dy, 0, hs - 1), np.clip(xs_s, 0, ws - 1)] = True
        glab, ng = ndi.label(GR)
        cut = np.zeros_like(GR)
        for k, sl in enumerate(ndi.find_objects(glab), 1):
            nrows = len({ri_ for ri_, g0, g1 in gap_segs
                         if glab[int(np.clip(round(rows[ri_]["Y"][(g0 + g1) // 2]) // f2, 0, hs - 1)),
                                 int(np.clip(rows[ri_]["X"][(g0 + g1) // 2] // f2, 0, ws - 1))] == k})
            if nrows >= CUT_MIN_ROWS:
                cut |= glab == k
        if cut.any():
            newrows = []
            for ri_, r in enumerate(rows):
                inc = cut[np.clip(np.round(r["Y"]).astype(int) // f2, 0, hs - 1), np.clip(r["X"] // f2, 0, ws - 1)]
                lab_, nl_ = ndi.label(~inc)
                for sl in ndi.find_objects(lab_):
                    a0, a1 = sl[0].start, sl[0].stop
                    if (a1 - a0) * RES < 2.0:
                        continue
                    q = {k_: (v_[a0:a1] if isinstance(v_, np.ndarray) else v_) for k_, v_ in r.items()}
                    newrows.append(q)
            # re-label blocks: block mask minus cut lines
            BM2 = BM & ~ndi.binary_dilation(cut, iterations=1)
            blab, _ = ndi.label(BM2)
            for q in newrows:
                m_ = len(q["X"]) // 2
                q["block"] = int(blab[int(np.clip(round(q["Y"][m_]) // f2, 0, hs - 1)), int(np.clip(q["X"][m_] // f2, 0, ws - 1))])
            rows = [q for q in newrows if q["block"] > 0]
            # trim ends to canopy again
            for q in rows:
                o = np.nonzero(ndi.gaussian_filter1d(q["occ"], 0.1 / RES) > 0.08)[0]
                if len(o):
                    for k_ in ("X", "Y", "occ", "tree", "dark"):
                        q[k_] = q[k_][o[0]:o[-1] + 1]
            rows = [q for q in rows if len(q["X"]) * RES >= 2.0]
            log(f"   {int(ndi.label(cut)[1])} cross-block track(s) found -> blocks split")

    # ---- row lattice: a block is a set of parallel rows at a constant spacing.
    # (1) pieces of the same physical row (split by a planting gap) are merged into one row,
    # (2) a row missing between two neighbours ~2x / 3x the spacing apart is re-inserted.
    if LATTICE:
        def profiles(X, Yax):
            Yi = np.clip(np.round(Yax).astype(int), 0, Hr - 1)
            Xc = np.clip(X, 0, Wr - 1)
            occ = np.zeros(len(X)); trf = np.zeros(len(X)); drk = np.zeros(len(X))
            for dy in range(-hb, hb + 1):
                yy = np.clip(Yi + dy, 0, Hr - 1)
                occ += C[yy, Xc]; trf += TREE[yy, Xc]; drk += DARK[yy, Xc] & ~C[yy, Xc]
            n_ = 2 * hb + 1
            return occ / n_, trf / n_, drk / n_

        def y_at(r, x):
            return float(np.interp(x, r["X"], r["Y"]))

        out_l, n_merged, n_filled = [], 0, 0
        for b in sorted({r["block"] for r in rows}):
            rs = sorted([r for r in rows if r["block"] == b], key=lambda r: r["X"][0])
            merged = True
            while merged:                                   # (1) merge collinear pieces
                merged = False
                for i in range(len(rs)):
                    for j in range(len(rs)):
                        A, Bq = rs[i], rs[j]
                        if i == j or Bq["X"][0] <= A["X"][-1]:
                            continue
                        if abs(y_at(A, A["X"][-1]) - y_at(Bq, Bq["X"][0])) < 0.3 * P and                                 (Bq["X"][0] - A["X"][-1]) * RES < LATTICE_MAX_GAP_M:
                            X = np.arange(A["X"][0], Bq["X"][-1] + 1)
                            Yax = np.interp(X, np.r_[A["X"], Bq["X"]], np.r_[A["Y"], Bq["Y"]])
                            o, t_, d_ = profiles(X, Yax)
                            rs[i] = dict(block=b, X=X, Y=Yax, occ=o, tree=t_, dark=d_)
                            rs.pop(j)
                            merged, n_merged = True, n_merged + 1
                            break
                    if merged:
                        break
            rs.sort(key=lambda r: np.median(r["Y"]))
            add = []
            for A, Bq in zip(rs[:-1], rs[1:]):              # (2) fill missing rows
                lo, hi = max(A["X"][0], Bq["X"][0]), min(A["X"][-1], Bq["X"][-1])
                if (hi - lo) * RES < 5:
                    continue
                X = np.arange(lo, hi + 1)
                ya, yb = np.interp(X, A["X"], A["Y"]), np.interp(X, Bq["X"], Bq["Y"])
                k = int(round(float(np.median(yb - ya)) / P))
                if k < 2 or k > 3 or abs(np.median(yb - ya) / P - k) > 0.25:
                    continue
                for m_ in range(1, k):
                    Yax = ya + (yb - ya) * m_ / k
                    o, t_, d_ = profiles(X, Yax)
                    add.append(dict(block=b, X=X, Y=Yax, occ=o, tree=t_, dark=d_, filled=True))
                    n_filled += 1
            out_l += rs + add
        rows = out_l
        # (3) neighbouring blocks separated only by 1-3 missing rows are one planting: fill + join
        rows.sort(key=lambda r: np.median(r["Y"]))
        relabel = {}
        find = lambda k: relabel.get(k, k) if relabel.get(k, k) == k else find(relabel[k])   # noqa: E731
        add = []
        for i, A in enumerate(rows):
            for Bq in rows[i + 1:]:
                if find(A["block"]) == find(Bq["block"]):
                    continue
                lo, hi = max(A["X"][0], Bq["X"][0]), min(A["X"][-1], Bq["X"][-1])
                if (hi - lo) * RES < 10:
                    continue
                X = np.arange(lo, hi + 1)
                ya, yb = np.interp(X, A["X"], A["Y"]), np.interp(X, Bq["X"], Bq["Y"])
                dyp = float(np.median(yb - ya)) / P
                k = int(round(dyp))
                if dyp < 0.5 or k > 4 or abs(dyp - k) > 0.25:
                    continue
                # only if no other row lies between them (they are true neighbours)
                if any(r_ is not A and r_ is not Bq and r_["X"][0] <= hi and r_["X"][-1] >= lo and
                       np.median(ya) + 0.5 * P < np.median(r_["Y"]) < np.median(yb) - 0.5 * P for r_ in rows):
                    continue
                relabel[find(Bq["block"])] = find(A["block"])
                for m_ in range(1, k):
                    Yax = ya + (yb - ya) * m_ / k
                    o, t_, d_ = profiles(X, Yax)
                    add.append(dict(block=A["block"], X=X, Y=Yax, occ=o, tree=t_, dark=d_, filled=True))
                    n_filled += 1
                break
        rows += add
        for r in rows:
            r["block"] = find(r["block"])
        if n_merged or n_filled:
            log(f"   row lattice: {n_merged} row pieces merged, {n_filled} missing rows filled")

    # ---- vine spacing (autocorrelation of occupancy along rows)
    acs = []
    for r in rows:
        if len(r["occ"]) > 10 / RES:
            p_, q_ = _period_from_profile(ndi.gaussian_filter1d(r["occ"], 2), RES, 0.8, 2.0)
            if p_ and q_ > 0.2 and 0.85 < p_ < 1.95:
                acs.append(p_)
    vine_sp = float(np.median(acs)) if len(acs) >= 3 else 1.2

    # ---- per-row products
    out_rows, vines, gaps = [], [], []
    for r in rows:
        X, Y, occ = r["X"], r["Y"], r["occ"]
        n = len(X)
        o = ndi.gaussian_filter1d(occ, 0.1 / RES)
        pres = o > 0.08
        pres = ndi.binary_closing(pres, np.ones(int(0.25 / RES)))
        pres[0] = pres[-1] = True if n > 2 else pres[0]
        lab, nl = ndi.label(pres)
        segs = ndi.find_objects(lab)
        # gaps
        glist = []
        for i in range(1, len(segs)):
            g0, g1 = segs[i - 1][0].stop, segs[i][0].start
            glen = (g1 - g0) * RES
            if glen >= 1.5 * vine_sp:
                glist.append((g0, g1, glen))
        occl = (r["tree"] > 0.3).mean()
        shadow = (r["dark"] > 0.5).mean()
        length_px = np.hypot(np.diff(X), np.diff(Y)).sum()
        gap_total = sum(g[2] for g in glist)
        if length_px * RES < 3 or shadow > 0.5:
            structure = "unassessable"
        elif any(g[2] >= GAP_INSPECT_M for g in glist):
            structure = "disrupted"
        else:
            structure = "regular"
        # vine segments: split long presence runs at occupancy minima ~ vine spacing
        cuts = []
        sp = vine_sp / RES
        for sl in segs:
            s0, s1 = sl[0].start, sl[0].stop
            if (s1 - s0) > 1.6 * sp:
                inv = -ndi.gaussian_filter1d(occ[s0:s1], 0.15 / RES)
                mins, _ = find_peaks(inv, distance=0.6 * sp, prominence=0.03)
                mins = [m for m in mins if m > 0.3 * sp and (s1 - s0 - m) > 0.3 * sp]
                pieces = [s0] + [s0 + m for m in mins] + [s1]
            else:
                pieces = [s0, s1]
            for a_, b_ in zip(pieces[:-1], pieces[1:]):
                cuts.append((a_, b_))
        # detection confidence: how much of the axis carries canopy, discounted for tree occlusion / shadow
        cov = float((r["occ"] > 0.08).mean()) if len(r["occ"]) else 0.0
        conf = float(np.clip(0.35 + 0.65 * min(1.0, cov / 0.7) * (1 - occl) * (1 - shadow), 0, 1))
        r.update(structure=structure, gaps=glist, cuts=cuts, length_px=length_px, conf=round(conf, 3))
        out_rows.append(r)

    # CIVE (Kataoka et al. 2003) + Otsu threshold computed on this region's row bands:
    # a training-free, self-calibrating canopy / soil separation (adapts to soil colour and light)
    cive_t = None
    if CANOPY_MODE == "cive":
        smp = []
        for r in out_rows:
            X, Y = r["X"], r["Y"]
            k_ = np.arange(0, len(X), 7)
            for dy in range(-hb, hb + 1, 2):
                yy_ = np.clip(np.round(Y[k_]).astype(int) + dy, 0, Hr - 1)
                ok_ = VAL[yy_, X[k_]] & ~TREE[yy_, X[k_]]
                smp.append(rrgb[yy_[ok_], X[k_][ok_]])
        smp = np.concatenate(smp) if smp else np.zeros((0, 3), np.uint8)
        if len(smp) > 500:
            cv_ = _cive(smp[None])[0]
            cive_t = float(_otsu(np.clip(cv_, -0.2, 0.6)))
    # canopy polygons per vine piece (raster -> contours in rotated frame)
    for ri, r in enumerate(out_rows):
        X, Y = r["X"], r["Y"]
        y0 = int(max(np.floor(Y.min()) - hb - 1, 0))
        y1 = int(min(np.ceil(Y.max()) + hb + 2, Hr))
        xa = int(X[0])
        yy = np.arange(y0, y1)[:, None]
        xb_ = int(X[-1]) + 1
        band = (np.abs(yy - Y[None, :xb_ - xa]) <= hb) & ~TREE[y0:y1, xa:xb_] & VAL[y0:y1, xa:xb_]
        if CANOPY_MODE == "cive" and cive_t is not None:
            cv_ = _cive(rrgb[y0:y1, xa:xb_])
            t_ = cive_t
            if CIVE_ROW_WEIGHT > 0 and band.sum() > 400:           # local (per-row) Otsu, blended with region
                t_ = CIVE_ROW_WEIGHT * float(_otsu(np.clip(cv_[band], -0.2, 0.6))) + (1 - CIVE_ROW_WEIGHT) * cive_t
            crop = ((cv_ > t_) & band).astype(np.uint8)
        elif CANOPY_MODE == "grabcut":
            crop = _grabcut_row(rrgb, TREE, VAL, X, Y, xa, xb_, y0, y1, hb, P) & band.astype(np.uint8)
        elif clf is not None:
            from .pixfeat import features
            F = features(rrgb[y0:y1, xa:xb_])
            crop = np.zeros(band.shape, np.uint8)
            if band.any():
                crop[band] = clf.predict_proba(F[band])[:, 1] > CLF_THRESHOLD
        else:
            crop = LO[y0:y1, xa:xb_].astype(np.uint8) & band.astype(np.uint8)
        crop = cv2.morphologyEx(crop, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        if CLOSE_K > 1:
            crop = cv2.morphologyEx(crop, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(CLOSE_K), int(CLOSE_K))))
        # plant segmentation along the row: occupancy runs, long runs split at minima (~ planting distance)
        occ_c = crop.sum(0).astype(float)
        pres_c = ndi.binary_closing(occ_c >= 2, np.ones(int(PLANT_JOIN_M / RES)))
        labc, _ = ndi.label(pres_c)
        cuts = []
        sp = max(vine_sp, PLANT_SP_MIN) / RES
        for sl in ndi.find_objects(labc):
            s0, s1 = sl[0].start, sl[0].stop
            if (s1 - s0) * RES < PLANT_MIN_M:
                continue
            if (s1 - s0) > 2.0 * sp:
                prof_ = ndi.gaussian_filter1d(occ_c[s0:s1], 0.12 / RES)
                mins, _ = find_peaks(-prof_, distance=0.8 * sp, prominence=0.25 * prof_.max())
                mins = [m for m in mins if m > 0.6 * sp and (s1 - s0 - m) > 0.6 * sp]
                pc = [s0] + [s0 + m for m in mins] + [s1]
            else:
                pc = [s0, s1]
            cuts += list(zip(pc[:-1], pc[1:]))
        def seg_polygon(m):
            if CLOSE_K > 1:
                m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((int(CLOSE_K), int(CLOSE_K)), np.uint8))
            if ERODE_PX > 0:
                m = cv2.erode(m, np.ones((int(ERODE_PX) * 2 + 1,) * 2, np.uint8))
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            polys = []
            for c in cnts:
                if cv2.contourArea(c) < (MIN_CONTOUR_M2 / RES ** 2):
                    continue
                c = c[:, 0, :].astype(float)
                wx, wy = rot_to_world(c[:, 0] + xa, c[:, 1] + y0)
                p = Polygon(np.c_[wx, wy]).buffer(0)
                if not p.is_empty:
                    polys.append(p)
            if not polys:
                return None
            pu = unary_union(polys)
            if pu.geom_type != "Polygon":                      # one plant = one polygon
                pu = pu.buffer(JOIN_BUF_M).buffer(-JOIN_BUF_M)
                if pu.geom_type != "Polygon":
                    pu = max(pu.geoms, key=lambda q: q.area)
            pu = pu.simplify(0.03)
            return pu if pu.area >= MIN_PIECE_M2 else None

        segs_m = []
        if PLANT_SPLIT == "watershed":
            from skimage.segmentation import watershed
            from skimage.feature import peak_local_max
            for sl in ndi.find_objects(labc):
                a_, b_ = sl[0].start, sl[0].stop
                if (b_ - a_) * RES < PLANT_MIN_M:
                    continue
                m = np.zeros_like(crop)
                m[:, a_:b_] = crop[:, a_:b_]
                if (b_ - a_) * RES <= PLANT_SPLIT_LEN:
                    segs_m.append(m)
                    continue
                dt = ndi.gaussian_filter(ndi.distance_transform_edt(m), 2)
                # markers: DT maxima >= PLANT_MIN_SEP apart along the row (one per plant body)
                pk = peak_local_max(dt, min_distance=int(PLANT_MIN_SEP / RES / 2), threshold_abs=1.5,
                                    labels=m.astype(int), exclude_border=False)
                pk = pk[np.argsort(pk[:, 1])]
                keep = []
                for q in pk:
                    if not keep or q[1] - keep[-1][1] >= PLANT_MIN_SEP / RES:
                        keep.append(q)
                    elif dt[q[0], q[1]] > dt[keep[-1][0], keep[-1][1]]:
                        keep[-1] = q
                if len(keep) <= 1:
                    segs_m.append(m)
                    continue
                mk = np.zeros(m.shape, np.int32)
                for k_, q in enumerate(keep, 1):
                    mk[q[0], q[1]] = k_
                lab_ = watershed(-dt, mk, mask=m.astype(bool))
                if (m.astype(bool) & (lab_ == 0)).any():       # pieces without a marker -> nearest plant
                    _, (iy_, ix_) = ndi.distance_transform_edt(lab_ == 0, return_indices=True)
                    lab_ = np.where(m.astype(bool), lab_[iy_, ix_], 0)
                for k_ in range(1, len(keep) + 1):
                    segs_m.append((lab_ == k_).astype(np.uint8))
        else:
            for a_, b_ in cuts:
                m = np.zeros_like(crop)
                m[:, a_:b_] = crop[:, a_:b_]
                segs_m.append(m)
        pieces = [p_ for p_ in (seg_polygon(m) for m in segs_m) if p_ is not None]
        r["vines"] = pieces
        # canopy half-width on each side (for inter-row edges)
        cy = np.nonzero(crop)
        if len(cy[0]):
            dev = cy[0] + y0 - Y[cy[1]]
            r["hw_up"] = float(np.clip(-np.percentile(dev, 5), 0.15 / RES, hb))
            r["hw_dn"] = float(np.clip(np.percentile(dev, 95), 0.15 / RES, hb))
        else:
            r["hw_up"] = r["hw_dn"] = 0.3 / RES

    # ---- inter-row strips between consecutive rows in the same block
    interrows = []
    by_block = {}
    for ri, r in enumerate(out_rows):
        by_block.setdefault(r["block"], []).append(ri)
    for b, idxs in by_block.items():
        idxs.sort(key=lambda i: np.median(out_rows[i]["Y"]))
        pairs = []
        for i in idxs:                                  # each row -> nearest overlapping row below it
            A = out_rows[i]
            best = None
            for j in idxs:
                if j == i:
                    continue
                Bq = out_rows[j]
                lo, hi = max(A["X"][0], Bq["X"][0]), min(A["X"][-1], Bq["X"][-1])
                if hi - lo < 2 / RES:
                    continue
                dy = np.median(np.interp(np.arange(lo, hi, 20), Bq["X"], Bq["Y"]) - np.interp(np.arange(lo, hi, 20), A["X"], A["Y"]))
                if 0.5 * P < dy < 1.6 * P and (best is None or dy < best[0]):
                    best = (dy, j)
            if best:
                pairs.append((i, best[1]))
        for i, j in pairs:
            A, Bq = out_rows[i], out_rows[j]
            lo, hi = max(A["X"][0], Bq["X"][0]), min(A["X"][-1], Bq["X"][-1])
            Xs = np.arange(lo, hi + 1, 10)
            Xs[-1] = hi
            ya = np.interp(Xs, A["X"], A["Y"]) + A["hw_dn"]
            yb = np.interp(Xs, Bq["X"], Bq["Y"]) - Bq["hw_up"]
            if np.any(yb - ya < 2):
                ya, yb = np.minimum(ya, yb - 2), np.maximum(yb, ya + 2)
            px = np.r_[Xs, Xs[::-1]]
            py = np.r_[ya, yb[::-1]]
            wx, wy = rot_to_world(px, py)
            poly = Polygon(np.c_[wx, wy]).buffer(0)
            # stats
            Xr = np.arange(lo, hi)
            yA = np.interp(Xr, A["X"], A["Y"]) + A["hw_dn"]
            yB = np.interp(Xr, Bq["X"], Bq["Y"]) - Bq["hw_up"]
            nv = nt = nd = tot = 0
            for k_ in range(0, len(Xr), 4):
                xk = Xr[k_]
                y_a, y_b = int(np.ceil(yA[k_])), int(np.floor(yB[k_]))
                if y_b <= y_a:
                    continue
                col = slice(y_a, y_b)
                vm = VAL[col, xk]
                tot += vm.sum()
                nv += (rveg[col, xk] & vm & ~TREE[col, xk]).sum()
                nt += (TREE[col, xk] & vm).sum()
                nd += (DARK[col, xk] & vm & ~rveg[col, xk].astype(bool)).sum()
            if tot == 0:
                continue
            fv, ft, fd = nv / tot, nt / tot, nd / tot
            if ft + fd > 0.5:
                cover = "unassessable"
            elif fv < 0.25:
                cover = "bare_soil"
            elif fv > 0.75:
                cover = "vegetation"
            else:
                cover = "mixed"
            interrows.append(dict(block=b, rows=(i, j), geometry=poly, cover=cover,
                                  veg_frac=round(float(fv), 3)))

    # ---- world geometries for rows & gaps
    for r in out_rows:
        step = max(1, int(2 / RES))
        k_ = np.r_[np.arange(0, len(r["X"]), step), len(r["X"]) - 1]
        wx, wy = rot_to_world(r["X"][k_], r["Y"][k_])
        r["geometry"] = LineString(np.c_[wx, wy]).simplify(0.05)
        r["gap_pts"] = []
        r["gap_lines"] = []
        for g0, g1, glen in r["gaps"]:
            gx0, gy0 = rot_to_world(r["X"][max(g0 - 1, 0)], r["Y"][max(g0 - 1, 0)])
            gx1, gy1 = rot_to_world(r["X"][min(g1, len(r["X"]) - 1)], r["Y"][min(g1, len(r["X"]) - 1)])
            r["gap_lines"].append(LineString([(float(gx0), float(gy0)), (float(gx1), float(gy1))]))
            if glen >= GAP_INSPECT_M:
                m_ = (g0 + g1) // 2
                gx_, gy_ = rot_to_world(r["X"][m_], r["Y"][m_])
                r["gap_pts"].append((Point(float(gx_), float(gy_)), glen))
    for r in out_rows:
        for key in ("X", "Y", "occ", "tree", "dark", "cuts"):
            r.pop(key, None)
    return dict(rows=out_rows, interrows=interrows, period=period, vine_spacing=vine_sp,
                angle=float(a), t_veg=t_veg)
