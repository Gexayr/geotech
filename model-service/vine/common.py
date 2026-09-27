"""Shared helpers: tile index, reading arbitrary windows from the tile grid, colour indices."""
import glob
import os
import re

import numpy as np
import rasterio
from rasterio.enums import Resampling

TILE_PX = 2048
TILE_RES = 0.025                      # m / px of the supplied tiles
TILE_M = TILE_PX * TILE_RES           # 51.2 m
CRS = "EPSG:32635"          # default working CRS (Sireț3); any metric CRS can be set with set_crs()


def set_crs(crs):
    """Set the metric working CRS of this run (outputs, zones and start point are expressed in it)."""
    global CRS
    CRS = str(crs)


def work_crs():
    return CRS

_NAME_RE = re.compile(r"siret3_r(\d{3})_c(\d{3})\.tif$", re.I)


class TileIndex:
    """Index of the supplied GeoTIFF tiles (a regular, non-overlapping grid)."""

    def __init__(self, tile_dir):
        self.tiles = {}
        for f in sorted(glob.glob(os.path.join(tile_dir, "*.tif"))):
            m = _NAME_RE.search(os.path.basename(f))
            if not m:
                continue
            with rasterio.open(f) as d:
                b = d.bounds
                assert abs(d.res[0] - TILE_RES) < 1e-6 and d.width == TILE_PX
            self.tiles[(int(m.group(1)), int(m.group(2)))] = (f, b)
        if not self.tiles:
            raise SystemExit(f"no tiles found in {tile_dir}")
        # grid origin (left / top of r000_c000), derived from any tile
        (r, c), (_, b) = next(iter(self.tiles.items()))
        self.x0 = b.left - c * TILE_M
        self.y0 = b.top + r * TILE_M
        rs = [k[0] for k in self.tiles]
        cs = [k[1] for k in self.tiles]
        self.rmin, self.rmax, self.cmin, self.cmax = min(rs), max(rs), min(cs), max(cs)

    @property
    def bounds(self):
        return (self.x0 + self.cmin * TILE_M, self.y0 - (self.rmax + 1) * TILE_M,
                self.x0 + (self.cmax + 1) * TILE_M, self.y0 - self.rmin * TILE_M)

    def read(self, left, top, width_m, height_m, res):
        """Read RGB (H,W,3 uint8) + valid mask for the box [left, left+w] x [top-h, top] at `res` m/px.
        Box edges are snapped to the `res` grid anchored at the tile origin."""
        f = int(round(res / TILE_RES))
        assert f >= 1 and TILE_PX % f == 0, "res must be a divisor-multiple of 2.5 cm"
        tp = TILE_PX // f
        c0 = int(np.floor((left - self.x0) / res))
        r0 = int(np.floor((self.y0 - top) / res))
        W = int(np.ceil(width_m / res))
        H = int(np.ceil(height_m / res))
        out = np.zeros((H, W, 3), np.uint8)
        valid = np.zeros((H, W), bool)
        for tr in range(r0 // tp, (r0 + H - 1) // tp + 1):
            for tc in range(c0 // tp, (c0 + W - 1) // tp + 1):
                t = self.tiles.get((tr, tc))
                if t is None:
                    continue
                with rasterio.open(t[0]) as d:
                    a = d.read(out_shape=(3, tp, tp), resampling=Resampling.average)
                a = a.transpose(1, 2, 0)
                ty, tx = tr * tp, tc * tp
                ys, ye = max(ty, r0), min(ty + tp, r0 + H)
                xs, xe = max(tx, c0), min(tx + tp, c0 + W)
                out[ys - r0:ye - r0, xs - c0:xe - c0] = a[ys - ty:ye - ty, xs - tx:xe - tx]
                valid[ys - r0:ye - r0, xs - c0:xe - c0] = a[ys - ty:ye - ty, xs - tx:xe - tx].max(2) > 0
        gx = self.x0 + c0 * res
        gy = self.y0 - r0 * res
        return out, valid, (gx, gy, res)


def exg(rgb):
    """Normalised excess-green index in [-1, 2]."""
    x = rgb.astype(np.float32)
    s = x.sum(2) + 1e-3
    r, g, b = x[..., 0] / s, x[..., 1] / s, x[..., 2] / s
    return 2 * g - r - b


def px_to_world(transform, rows, cols):
    gx, gy, res = transform
    return gx + (np.asarray(cols) + 0.5) * res, gy - (np.asarray(rows) + 0.5) * res


def world_to_px(transform, xs, ys):
    gx, gy, res = transform
    return (gy - np.asarray(ys)) / res - 0.5, (np.asarray(xs) - gx) / res - 0.5
