"""Generic image source with the same interface as TileIndex, so the whole pipeline runs on any image:
  * a georeferenced GeoTIFF in a metric CRS (used as is)
  * a GeoTIFF in geographic coordinates (warped on the fly to its local UTM zone)
  * a plain JPG / PNG without georeferencing (the user gives the ground resolution in m/px)
Everything outside the area of interest (AOI) is reported as no-data, so the pipeline only looks there.
"""
from collections import namedtuple

import numpy as np
import rasterio
from rasterio.crs import CRS as RCRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds

Bounds = namedtuple("Bounds", "left bottom right top")
VTILE_M = 51.2


def utm_crs(lon, lat):
    return RCRS.from_epsg((32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1)


def _read_box(ds, transform, left, top, right, bottom, W, H, bands):
    """Read [left,right]x[bottom,top] into an (bands,H,W) array, clipping to the dataset extent
    (works for WarpedVRT, which does not allow boundless reads)."""
    out = np.zeros((len(bands), H, W), np.float32)
    inv = ~transform
    c0, r0 = inv * (left, top)
    c1, r1 = inv * (right, bottom)
    cc0, rr0 = max(c0, 0), max(r0, 0)
    cc1, rr1 = min(c1, ds.width), min(r1, ds.height)
    if cc1 <= cc0 or rr1 <= rr0:
        return out, np.zeros((H, W), bool)
    sx, sy = W / (c1 - c0), H / (r1 - r0)                    # output px per source px
    ox0, oy0 = int(round((cc0 - c0) * sx)), int(round((rr0 - r0) * sy))
    ox1, oy1 = int(round((cc1 - c0) * sx)), int(round((rr1 - r0) * sy))
    ox1, oy1 = min(max(ox1, ox0 + 1), W), min(max(oy1, oy0 + 1), H)
    from rasterio.windows import Window
    a = ds.read(bands, window=Window(cc0, rr0, cc1 - cc0, rr1 - rr0), out_shape=(len(bands), oy1 - oy0, ox1 - ox0),
                resampling=Resampling.average)
    out[:, oy0:oy1, ox0:ox1] = a
    m = np.zeros((H, W), bool)
    m[oy0:oy1, ox0:ox1] = True
    return out, m


class ImageSource:
    def __init__(self, path, gsd=None, aoi=None):
        self.path = path
        src = rasterio.open(path)
        self._src = src
        georef = src.crs is not None and not src.transform.is_identity
        if georef and src.crs.is_geographic:
            c = src.xy(src.height // 2, src.width // 2)
            self.ds = WarpedVRT(src, crs=utm_crs(*c), resampling=Resampling.bilinear)
            self.kind = "geotiff (warped to UTM)"
        elif georef:
            self.ds = src
            self.kind = "geotiff"
        else:
            g = float(gsd or 0.03)
            self.ds = src
            self._tr = from_origin(0, src.height * g, g, g)
            self.kind = f"plain image, {g} m/px"
        self.transform = getattr(self, "_tr", None) or self.ds.transform
        self.crs = self.ds.crs if georef else None
        self.width, self.height = self.ds.width, self.ds.height
        self.gsd = abs(self.transform.a)
        self.aoi = aoi
        L, T = self.transform * (0, 0)
        R, B = self.transform * (self.width, self.height)
        full = Bounds(min(L, R), min(B, T), max(L, R), max(B, T))
        b = aoi.bounds if aoi is not None else full
        b = Bounds(max(b[0], full.left), max(b[1], full.bottom), min(b[2], full.right), min(b[3], full.top))
        self._bounds = b
        # virtual 51.2 m tiles over the AOI (used by the waste scan and the route coverage mask)
        self.tiles = {}
        nx = int(np.ceil((b.right - b.left) / VTILE_M))
        ny = int(np.ceil((b.top - b.bottom) / VTILE_M))
        for r in range(ny):
            for c in range(nx):
                l, t = b.left + c * VTILE_M, b.top - r * VTILE_M
                self.tiles[(r, c)] = (path, Bounds(l, t - VTILE_M, l + VTILE_M, t))

    @property
    def bounds(self):
        return tuple(self._bounds)

    def read(self, left, top, width_m, height_m, res):
        W, H = int(np.ceil(width_m / res)), int(np.ceil(height_m / res))
        right, bottom = left + W * res, top - H * res
        nb = min(3, self.ds.count)
        a, inside = _read_box(self.ds, self.transform, left, top, right, bottom, W, H, list(range(1, nb + 1)))
        if nb < 3:
            a = np.repeat(a[:1], 3, 0)
        rgb = a.transpose(1, 2, 0)
        if self.ds.dtypes[0] != "uint8":                # 16-bit etc. -> stretch to 8 bit
            hi = np.percentile(rgb[rgb > 0], 99.5) if (rgb > 0).any() else 1
            rgb = rgb / hi * 255
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        valid = (rgb.max(2) > 0) & inside
        if self.ds.count >= 4:
            al, _ = _read_box(self.ds, self.transform, left, top, right, bottom, W, H, [4])
            valid &= al[0] > 0
        if self.aoi is not None:
            tr = from_origin(left, top, res, res)
            valid &= rasterize([(self.aoi, 1)], out_shape=(H, W), transform=tr).astype(bool)
        rgb = rgb.copy()
        rgb[~valid] = 0
        return rgb, valid, (left, top, res)

    def preview(self, max_px=2048):
        s = max(self.width, self.height) / max_px
        w, h = max(1, int(self.width / s)), max(1, int(self.height / s))
        nb = min(3, self.ds.count)
        a = self.ds.read(list(range(1, nb + 1)), out_shape=(nb, h, w), resampling=Resampling.average)
        if nb < 3:
            a = np.repeat(a[:1], 3, 0)
        rgb = a.transpose(1, 2, 0)
        if rgb.dtype != np.uint8:
            x = rgb.astype(np.float32)
            rgb = np.clip(x / np.percentile(x[x > 0], 99.5) * 255, 0, 255).astype(np.uint8)
        return rgb, self.width / w                      # preview image, full-res px per preview px


class MosaicSource:
    """Any folder of GeoTIFF tiles (any names, any size, overlaps allowed) read as one mosaic.

    Tiles in a geographic CRS are warped on the fly to the local UTM zone. Same interface as
    ImageSource / TileIndex: bounds, tiles (virtual 51.2 m grid), read(left, top, w, h, res), crs, gsd.
    """

    def __init__(self, paths, aoi=None):
        self.paths = sorted(paths)
        if not self.paths:
            raise SystemExit("no GeoTIFF tiles found")
        first = rasterio.open(self.paths[0])
        if first.crs is None:
            raise SystemExit("tiles have no georeference - use a single image with --gsd instead")
        target = first.crs
        if first.crs.is_geographic:
            c = first.xy(first.height // 2, first.width // 2)
            target = utm_crs(*c)
        self.crs = target
        self.ds = []
        for p in self.paths:
            s = rasterio.open(p)
            self.ds.append(s if s.crs == target else WarpedVRT(s, crs=target, resampling=Resampling.bilinear))
        self.gsd = float(np.median([abs(d.transform.a) for d in self.ds]))
        L = min(d.bounds.left for d in self.ds)
        B = min(d.bounds.bottom for d in self.ds)
        R = max(d.bounds.right for d in self.ds)
        T = max(d.bounds.top for d in self.ds)
        full = Bounds(L, B, R, T)
        self.aoi = aoi
        b = aoi.bounds if aoi is not None else full
        b = Bounds(max(b[0], L), max(b[1], B), min(b[2], R), min(b[3], T))
        self._bounds = b
        self.kind = f"mosaic of {len(self.paths)} GeoTIFF tiles"
        self.tiles = {}
        nx = int(np.ceil((b.right - b.left) / VTILE_M))
        ny = int(np.ceil((b.top - b.bottom) / VTILE_M))
        for r in range(ny):
            for c in range(nx):
                l, t = b.left + c * VTILE_M, b.top - r * VTILE_M
                self.tiles[(r, c)] = (self.paths[0], Bounds(l, t - VTILE_M, l + VTILE_M, t))

    @property
    def bounds(self):
        return tuple(self._bounds)

    def read(self, left, top, width_m, height_m, res):
        from rasterio.windows import from_bounds as wfb
        W, H = int(np.ceil(width_m / res)), int(np.ceil(height_m / res))
        right, bottom = left + W * res, top - H * res
        rgb = np.zeros((H, W, 3), np.uint8)
        valid = np.zeros((H, W), bool)
        for d in self.ds:
            b = d.bounds
            if b.left >= right or b.right <= left or b.bottom >= top or b.top <= bottom:
                continue
            nb = min(3, d.count)
            a, _ = _read_box(d, d.transform, left, top, right, bottom, W, H, list(range(1, nb + 1)))
            if nb < 3:
                a = np.repeat(a[:1], 3, 0)
            a = a.transpose(1, 2, 0)
            if d.dtypes[0] != "uint8":
                hi = np.percentile(a[a > 0], 99.5) if (a > 0).any() else 1
                a = a / hi * 255
            a = np.clip(a, 0, 255).astype(np.uint8)
            m = (a.max(2) > 0) & ~valid
            rgb[m] = a[m]
            valid |= m
        if self.aoi is not None:
            tr = from_origin(left, top, res, res)
            valid &= rasterize([(self.aoi, 1)], out_shape=(H, W), transform=tr).astype(bool)
        rgb[~valid] = 0
        return rgb, valid, (left, top, res)


def open_source(path, gsd=None, aoi=None):
    """Pick the right reader: Sireț3 tile folder, any tile folder, or a single image."""
    import glob
    import os
    if os.path.isdir(path):
        tifs = sorted(glob.glob(os.path.join(path, "**", "*.tif"), recursive=True) +
                      glob.glob(os.path.join(path, "**", "*.tiff"), recursive=True))
        from .common import TileIndex, _NAME_RE
        if tifs and all(_NAME_RE.search(os.path.basename(t)) for t in tifs) and aoi is None:
            ti = TileIndex(path)
            ti.crs, ti.kind = RCRS.from_epsg(32635), "Sireț3 challenge tiles"
            ti.gsd = 0.025
            return ti
        return MosaicSource(tifs, aoi=aoi)
    return ImageSource(path, gsd=gsd, aoi=aoi)
