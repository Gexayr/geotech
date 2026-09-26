"""Classical-CV canopy/row/inter-row detection — no trained model, no GPU.

Shared by the offline batch pre-annotator (scripts/classical_pre_annotate.py,
which runs this over all 311 supplied tiles and writes one CVAT XML) and the
live upload path (api/site.py, which runs it on a single newly-uploaded
tile). One implementation, two callers — see the batch script's docstring
for the method and its calibrated accuracy against the 2 ground-truth tiles.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import rasterio
from scipy.ndimage import binary_closing, gaussian_filter1d
from scipy.signal import find_peaks

PIXEL_SIZE_M = 0.025  # 2.5 cm/px, per the annotation rules
PLANT_SPACING_PX = int(1.2 / PIXEL_SIZE_M)  # 48px — rules' own fallback spacing
GAP_DISRUPTED_PX = int(5.0 / PIXEL_SIZE_M)  # 200px — a 5m gap marks a row "disrupted"
MIN_CANOPY_AREA_PX = int(0.2 / PIXEL_SIZE_M**2)  # 320px — rules' min leaf-clump size

MIN_HOUGH_VOTES = 15  # below this, the tile likely has no real row structure
MIN_VEG_FRACTION = 0.03
MAX_VEG_FRACTION = 0.45
CANOPY_WIDTH_FACTOR = 0.5  # calibrated against ground truth on the 2 known tiles (+2.4% area error)


def vegetation_mask(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[0].astype(np.float32), rgb[1].astype(np.float32), rgb[2].astype(np.float32)
    exg = 2 * g - r - b
    span = exg.max() - exg.min()
    if span < 1e-6:
        return np.zeros(exg.shape, dtype=np.uint8)
    norm = np.clip((exg - exg.min()) / span * 255, 0, 255).astype(np.uint8)
    _, mask = cv2.threshold(norm, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return mask


def dominant_row_angle(mask: np.ndarray) -> tuple[float, int]:
    """Returns (angle_degrees, hough_vote_count). Vote count is used as a
    confidence signal — low votes mean "this tile probably isn't rows"."""
    edges = (mask * 255).astype(np.uint8)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60, minLineLength=120, maxLineGap=20)
    if lines is None or len(lines) < MIN_HOUGH_VOTES:
        return 0.0, 0 if lines is None else len(lines)
    lines = lines.reshape(-1, 4)
    angles = np.degrees(np.arctan2(lines[:, 3] - lines[:, 1], lines[:, 2] - lines[:, 0])) % 180
    hist, edges_h = np.histogram(angles, bins=180, range=(0, 180))
    dominant_bin = int(np.argmax(hist))
    near = np.abs(angles - edges_h[dominant_bin]) < 5
    refined = float(np.mean(angles[near])) if near.any() else float(edges_h[dominant_bin])
    return refined, int(hist[dominant_bin])


def rotate_mask(mask: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray, int]:
    h, w = mask.shape
    diag = int(np.ceil(np.sqrt(h**2 + w**2)))
    center = (w / 2, h / 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    m[0, 2] += (diag - w) / 2
    m[1, 2] += (diag - h) / 2
    rotated = cv2.warpAffine(mask.astype(np.uint8), m, (diag, diag), flags=cv2.INTER_NEAREST)
    return rotated, m, diag


def invert_point(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    m_inv = cv2.invertAffineTransform(m)
    px = m_inv[0, 0] * x + m_inv[0, 1] * y + m_inv[0, 2]
    py = m_inv[1, 0] * x + m_inv[1, 1] * y + m_inv[1, 2]
    return float(px), float(py)


def process_tile(path: Path) -> dict:
    """Returns {rows: [...], canopies: [...], interrows: [...]} in PIXEL
    coordinates, empty on tiles that don't look like vineyard rows."""
    with rasterio.open(path) as ds:
        arr = ds.read([1, 2, 3])
    h, w = arr.shape[1], arr.shape[2]
    mask = vegetation_mask(arr)
    veg_frac = mask.mean()

    if veg_frac < MIN_VEG_FRACTION or veg_frac > MAX_VEG_FRACTION:
        return {"rows": [], "canopies": [], "interrows": []}

    angle, votes = dominant_row_angle(mask)
    if votes < MIN_HOUGH_VOTES:
        return {"rows": [], "canopies": [], "interrows": []}

    rot_mask, m, diag = rotate_mask(mask, angle)
    profile = rot_mask.sum(axis=1).astype(np.float64)
    if profile.max() == 0:
        return {"rows": [], "canopies": [], "interrows": []}
    smooth = gaussian_filter1d(profile, sigma=12)
    peaks, _ = find_peaks(smooth, distance=90, height=max(smooth.max() * 0.08, 1))
    if len(peaks) == 0:
        return {"rows": [], "canopies": [], "interrows": []}

    band_half = 18  # px either side of a row's centre line
    vineyard_id = f"AUTO-{path.stem}"  # not merged across tiles — see the batch script's docstring

    rows_out = []
    canopies_out = []
    row_bands = []  # (y_center, x_start, x_end) in rotated frame, for interrow construction

    for i, py in enumerate(peaks):
        y0, y1 = max(0, py - band_half), min(diag, py + band_half)
        band = rot_mask[y0:y1, :]
        col_coverage = band.mean(axis=0)
        covered = col_coverage > 0.15
        xs = np.where(covered)[0]
        if len(xs) < 20:
            continue
        x_start, x_end = int(xs.min()), int(xs.max())
        row_bands.append((py, x_start, x_end))

        # row_structure: look for the largest gap in coverage along the row.
        sub = covered[x_start : x_end + 1]
        # Close small noise-driven holes (thin shadows, texture gaps within a
        # single canopy) before measuring gaps — otherwise per-column
        # threshold noise chains into false "disrupted" calls almost
        # everywhere. Only a gap that survives a ~1m closing is a real one.
        noise_close_px = 81
        sub_closed = binary_closing(sub, structure=np.ones(noise_close_px, dtype=bool))
        max_gap = 0
        cur_gap = 0
        for v in sub_closed:
            if not v:
                cur_gap += 1
                max_gap = max(max_gap, cur_gap)
            else:
                cur_gap = 0
        coverage_ratio = sub.mean()
        if coverage_ratio < 0.25:
            structure = "unassessable"
        elif max_gap >= GAP_DISRUPTED_PX:
            structure = "disrupted"
        else:
            structure = "regular"

        p0 = invert_point(m, x_start, py)
        p1 = invert_point(m, x_end, py)
        row_id = f"{vineyard_id}-R{i + 1:02d}"
        rows_out.append(
            {
                "points": [p0, p1],
                "vineyard_id": vineyard_id,
                "row_id": row_id,
                "row_structure": structure,
            }
        )

        # canopies: split covered stretches into ~1.2m pieces (rules' own
        # fallback for "no visible narrowing"), each as a small quad along
        # the row in rotated space, mapped back to image coordinates.
        in_run = False
        run_start = 0
        for x in range(x_start, x_end + 2):
            v = covered[x] if x <= x_end else False
            if v and not in_run:
                in_run = True
                run_start = x
            elif not v and in_run:
                in_run = False
                run_len = x - run_start
                n_pieces = max(1, round(run_len / PLANT_SPACING_PX))
                piece_len = run_len / n_pieces
                for k in range(n_pieces):
                    px0 = run_start + k * piece_len
                    px1 = run_start + (k + 1) * piece_len
                    if (px1 - px0) < 4:
                        continue
                    half_w = band_half * CANOPY_WIDTH_FACTOR
                    corners_rot = [
                        (px0, py - half_w),
                        (px1, py - half_w),
                        (px1, py + half_w),
                        (px0, py + half_w),
                    ]
                    corners_img = [invert_point(m, cx, cy) for cx, cy in corners_rot]
                    area_px = (px1 - px0) * (2 * half_w)
                    if area_px < MIN_CANOPY_AREA_PX:
                        continue
                    canopies_out.append({"points": corners_img, "vineyard_id": vineyard_id})

    # interrow_area: rotated-frame rectangle between each pair of adjacent
    # row bands, clipped to their overlapping x-extent.
    interrows_out = []
    row_bands.sort(key=lambda b: b[0])
    for i in range(len(row_bands) - 1):
        y_a, xa0, xa1 = row_bands[i]
        y_b, xb0, xb1 = row_bands[i + 1]
        x0, x1 = max(xa0, xb0), min(xa1, xb1)
        if x1 - x0 < 20:
            continue
        top = y_a + band_half * 0.6
        bottom = y_b - band_half * 0.6
        if bottom - top < 5:
            continue
        strip = rot_mask[int(top) : int(bottom), int(x0) : int(x1)]
        cover = strip.mean() if strip.size else 0.0
        cover_attr = "vegetation" if cover > 0.75 else "bare_soil" if cover < 0.25 else "mixed"
        corners_rot = [(x0, top), (x1, top), (x1, bottom), (x0, bottom)]
        corners_img = [invert_point(m, cx, cy) for cx, cy in corners_rot]
        interrows_out.append(
            {"points": corners_img, "vineyard_id": vineyard_id, "interrow_cover": cover_attr}
        )

    return {"rows": rows_out, "canopies": canopies_out, "interrows": interrows_out}
