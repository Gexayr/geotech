"""Synthetic vineyard orthomosaics with known ground truth (for tests on 'any map').

make_vineyard() draws soil with tractor tracks, a grass margin, a dirt road, and a vineyard block of
parallel rows (any angle / spacing) made of individual plants with shadows, plus planting gaps.
Returns the GeoTIFF path and the truth (row count, spacing, gap centres, canopy area).
"""
import numpy as np
import cv2
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling


def make_vineyard(path, angle_deg=30.0, spacing=2.6, plant_spacing=1.2, size_m=90.0, block=(55.0, 40.0),
                  gsd=0.05, crs="EPSG:32633", origin=(500000.0, 5100000.0), gaps=((3, 0.35, 7.0),), seed=0,
                  latlon=False):
    rng = np.random.default_rng(seed)
    n = int(size_m / gsd)
    # soil: brown with texture and tractor tracks along the rows
    img = np.zeros((n, n, 3), np.float32)
    base = np.array([150, 125, 95], np.float32) * rng.uniform(0.85, 1.1)
    img[:] = base
    img += cv2.GaussianBlur(rng.normal(0, 14, (n, n)).astype(np.float32), (0, 0), 2)[..., None]
    a = np.deg2rad(angle_deg)
    d = np.array([np.cos(a), -np.sin(a)])               # row direction in image coords (x right, y down)
    nrm = np.array([-d[1], d[0]])
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) * gsd
    c = np.array([size_m / 2, size_m / 2])
    u = (xx - c[0]) * d[0] + (yy - c[1]) * d[1]            # along-row coordinate (m)
    v = (xx - c[0]) * nrm[0] + (yy - c[1]) * nrm[1]         # across-row coordinate (m)
    img -= (10 * np.cos(2 * np.pi * v / (spacing / 3)) ** 8)[..., None]   # tracks
    # grass margin + dirt road
    grass = (xx < 8) | (yy > size_m - 8)
    img[grass] = np.array([85, 130, 60], np.float32) + rng.normal(0, 12, (grass.sum(), 3))
    road = np.abs(u - block[0] / 2 - 6) < 2.0
    img[road & ~grass] = np.array([200, 185, 160], np.float32)
    # vineyard rows of individual plants with shadows
    n_rows = int(block[1] / spacing) + 1
    L0 = -block[0] / 2
    canopy = np.zeros((n, n), np.uint8)
    shadow = np.zeros((n, n), np.uint8)
    gap_centres = []
    for k in range(n_rows):
        off = -block[1] / 2 + k * spacing
        for g_row, g_pos, g_len in gaps:
            if g_row == k:
                gap_centres.append(c + d * (L0 + g_pos * block[0]) + nrm * off)
        s = 0.0
        while s <= block[0]:
            in_gap = any(g_row == k and abs(s - g_pos * block[0]) < g_len / 2 for g_row, g_pos, g_len in gaps)
            if not in_gap:
                p = c + d * (L0 + s) + nrm * (off + rng.normal(0, 0.04))
                ax = (int(rng.uniform(0.35, 0.5) / gsd), int(rng.uniform(0.18, 0.28) / gsd))
                cen = (int(p[0] / gsd), int(p[1] / gsd))
                cv2.ellipse(canopy, cen, ax, -angle_deg, 0, 360, 1, -1)
                cv2.ellipse(shadow, (cen[0] + int(0.25 / gsd), cen[1] + int(0.25 / gsd)), ax, -angle_deg, 0, 360, 1, -1)
            s += plant_spacing
    img[(shadow > 0) & (canopy == 0)] *= 0.55
    leaf = np.array([70, 125, 45], np.float32)
    m = canopy > 0
    img[m] = leaf + rng.normal(0, 14, (m.sum(), 3))
    img = np.clip(img, 1, 255).astype(np.uint8)
    tr = from_origin(origin[0], origin[1] + size_m, gsd, gsd)
    prof = dict(driver="GTiff", width=n, height=n, count=3, dtype="uint8", crs=crs, transform=tr)
    if not latlon:
        with rasterio.open(path, "w", **prof) as dst:
            dst.write(img.transpose(2, 0, 1))
    else:                                               # store in geographic coordinates (tests warping)
        dt, w, h = calculate_default_transform(crs, "EPSG:4326", n, n, *rasterio.transform.array_bounds(n, n, tr))
        out = np.zeros((3, h, w), np.uint8)
        reproject(img.transpose(2, 0, 1), out, src_transform=tr, src_crs=crs, dst_transform=dt, dst_crs="EPSG:4326",
                  resampling=Resampling.bilinear)
        with rasterio.open(path, "w", **dict(prof, width=w, height=h, crs="EPSG:4326", transform=dt)) as dst:
            dst.write(out)
    gx = [(origin[0] + p[0], origin[1] + size_m - p[1]) for p in gap_centres]
    return dict(path=path, n_rows=n_rows, spacing=spacing, gaps=gx, canopy_m2=float(m.sum() * gsd * gsd),
                crs=crs, start=(origin[0] + 4.0, origin[1] + 4.0))
