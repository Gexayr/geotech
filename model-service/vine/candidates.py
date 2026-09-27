"""Stage 1 - coarse vineyard candidates.

Vine rows are a periodic green pattern (row spacing ~2-3.8 m).  On a 0.2 m/px mosaic we compute,
for sliding 12.8 m windows, the FFT power of the high-passed excess-green image and measure
  * score : share of the (non-DC) energy in the row-period annulus inside the strongest angular sector
  * sharp : how concentrated that sector's energy is radially (periodic rows -> sharp peak,
            a single straight edge such as a road border -> energy spread along the whole ray)
  * angle : direction of the row-frequency vector (rows run perpendicular to it)
Hysteresis thresholding + orientation consistency gives candidate areas.
"""
import numpy as np
import cv2
from scipy import ndimage as ndi
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .common import exg

RES = 0.2
SHARP_LOW, SHARP_SEED = 0.62, 0.75   # radial-sharpness thresholds (grow / seed) with harmonics counted
ANGLE_TOL_DEG = 7.0         # cells within this of a region's row direction belong to it
SHARP_HARMONICS = 1.0       # count 2nd-harmonic energy as row evidence (single edges have none)
ROW_PERIOD = (2.0, 3.8)      # expected vine row spacing (m); CLI --row-spacing
N, S, NS = 64, 8, 18


def periodicity_maps(rgb, valid, res=RES, pmin=None, pmax=None):
    pmin = ROW_PERIOD[0] if pmin is None else pmin
    pmax = ROW_PERIOD[1] if pmax is None else pmax
    g = exg(rgb)
    g[~valid] = 0
    g = g - cv2.GaussianBlur(g, (0, 0), 8)
    g[~valid] = 0
    H, W = g.shape
    win = np.outer(np.hanning(N), np.hanning(N)).astype(np.float32)
    fy = np.fft.fftfreq(N)[:, None]
    fx = np.fft.rfftfreq(N)[None, :]
    fr = np.hypot(fx, fy)
    per = np.where(fr > 0, 1 / np.maximum(fr, 1e-9), 1e9) * res
    th = np.mod(np.arctan2(fy, fx) * np.ones_like(fx), np.pi)
    band = (per >= pmin) & (per <= pmax)
    use = ((per < 12) & (fr > 0)).ravel().astype(np.float32)
    wide = (per >= 0.8) & (per <= 12)

    def sectors(m):
        return np.stack([(m & (np.abs(((th - (k + .5) * np.pi / NS) + np.pi / 2) % np.pi - np.pi / 2)
                                < np.pi / NS)).ravel() for k in range(NS)], 1).astype(np.float32)
    harm = (per >= pmin / 2) & (per < pmin)                 # 2nd harmonic of crisp rows
    sect, sectw, secth = sectors(band), sectors(wide), sectors(harm)
    vfull = ndi.uniform_filter(valid.astype(np.float32), N)
    ny, nx = (H - N) // S + 1, (W - N) // S + 1
    score = np.zeros((ny, nx), np.float32)
    sharp = np.zeros_like(score)
    ang = np.zeros_like(score)
    for i in range(ny):
        vf = vfull[i * S + N // 2, np.arange(nx) * S + N // 2]
        idx = np.where(vf > 0.6)[0]
        if len(idx) == 0:
            continue
        rows = g[i * S:i * S + N]
        wins = np.stack([rows[:, x * S:x * S + N] for x in idx]) * win
        P = (np.abs(np.fft.rfft2(wins)) ** 2).reshape(len(idx), -1)
        sp, sw, tot = P @ sect, P @ sectw, P @ use + 1e-9
        sh_ = P @ secth
        k = sp.argmax(1)
        kk = np.arange(len(idx))
        pk = sp[kk, k] + 0.5 * (sp[kk, (k + 1) % NS] + sp[kk, (k - 1) % NS])
        score[i, idx] = pk / tot
        sharp[i, idx] = (sp[kk, k] + SHARP_HARMONICS * sh_[kk, k]) / (sw[kk, k] + 1e-9)
        ang[i, idx] = (k + .5) * np.pi / NS
    return score, sharp, ang


def candidate_regions(score, sharp, ang, min_area=600.0):
    cell = S * RES
    c, s = np.cos(2 * ang) * score, np.sin(2 * ang) * score
    cs, ss, sm = ndi.uniform_filter(c, 5), ndi.uniform_filter(s, 5), ndi.uniform_filter(score, 5)
    coh = np.hypot(cs, ss) / (sm + 1e-9)
    sc = ndi.uniform_filter(score, 3)
    sh = ndi.uniform_filter(sharp, 5)
    low = ndi.binary_opening((sc > 0.12) & (coh > 0.6) & (sh > SHARP_LOW), iterations=1)
    lab, n = ndi.label(low)
    out = []
    for i in range(1, n + 1):
        m = lab == i
        if (sc * (sh > SHARP_SEED) * m).max() < 0.25:
            continue
        # one region per row direction: plantings with different row angles are split apart
        w = np.bincount(((ang[m] % np.pi) / np.pi * NS).astype(int) % NS, weights=score[m], minlength=NS)
        peaks = [k for k in range(NS) if w[k] >= w[(k - 1) % NS] and w[k] >= w[(k + 1) % NS] and w[k] > 0.15 * w.max()]
        for k in sorted(peaks, key=lambda k: -w[k]):
            a = (k + .5) * np.pi / NS
            d = np.abs(((ang - a) + np.pi / 2) % np.pi - np.pi / 2)
            mk = m & (d < np.deg2rad(ANGLE_TOL_DEG))
            mk = (ndi.binary_closing(mk, iterations=2) & low & (d < np.deg2rad(ANGLE_TOL_DEG))) | mk
            lab_k, nk = ndi.label(ndi.binary_fill_holes(ndi.binary_closing(mk, iterations=1)))
            for j in range(1, nk + 1):
                m2 = lab_k == j
                if m2.sum() * cell ** 2 < min_area or (sc * (sh > SHARP_SEED) * m2).max() < 0.25:
                    continue
                cs2, ss2 = cs[m2 & (d < np.deg2rad(ANGLE_TOL_DEG))], ss[m2 & (d < np.deg2rad(ANGLE_TOL_DEG))]
                a2 = 0.5 * np.arctan2(ss2.sum(), cs2.sum()) % np.pi if cs2.size else a
                out.append((m2, a2))
    return out


def mask_to_polygon(m, transform, cell_offset):
    """Coarse cell mask -> shapely polygon in world coordinates."""
    gx, gy, res = transform
    polys = []
    cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        c = c[:, 0, :].astype(float)
        if len(c) < 3:
            continue
        # cell (j,i) centre -> mosaic px (j*S + N/2, i*S + N/2)
        xs = gx + (c[:, 0] * S + cell_offset) * res
        ys = gy - (c[:, 1] * S + cell_offset) * res
        p = Polygon(np.c_[xs, ys]).buffer(S * res * 0.75)
        if p.is_valid and not p.is_empty:
            polys.append(p)
    return unary_union(polys)


def find_candidates(ti, log=print):
    L, B, R, T = ti.bounds
    rgb, valid, tr = ti.read(L, T, R - L, T - B, RES)
    log(f"mosaic {rgb.shape} at {RES} m/px")
    score, sharp, ang = periodicity_maps(rgb, valid)
    regions = candidate_regions(score, sharp, ang)
    cands = []
    for m, a in regions:
        # rows run perpendicular to the frequency vector (image coords: x right, y down)
        cands.append(dict(geometry=mask_to_polygon(m, tr, N / 2), freq_angle=float(a)))
    cands = merge_candidates(cands)
    log(f"{len(cands)} candidate vineyard regions")
    return cands, (rgb, valid, tr)


def merge_candidates(cands, dist=10.0, max_dangle=np.deg2rad(3)):
    """Join candidate regions of the same planting split by a weedy / grassy strip (same row direction)."""
    cands = list(cands)
    changed = True
    while changed:
        changed = False
        for i in range(len(cands)):
            for j in range(i + 1, len(cands)):
                a, b = cands[i], cands[j]
                da = abs((a["freq_angle"] - b["freq_angle"] + np.pi / 2) % np.pi - np.pi / 2)
                if da < max_dangle and a["geometry"].distance(b["geometry"]) < dist:
                    wa, wb = a["geometry"].area, b["geometry"].area
                    ang = a["freq_angle"] if wa >= wb else b["freq_angle"]
                    geom = a["geometry"].union(b["geometry"]).buffer(dist / 2).buffer(-dist / 2)
                    cands[i] = dict(geometry=geom, freq_angle=float(ang))
                    cands.pop(j)
                    changed = True
                    break
            if changed:
                break
    return cands
