"""Prepare the web interface data: layers in EPSG:4326 (display only; stored values stay in EPSG:32635
metres), a Web-Mercator orthomosaic image, and a summary JSON."""
import json
import os
import shutil

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from PIL import Image

from .common import TileIndex


def ortho_image(ti, out_jpg, res=0.25):
    L, B, R, T = ti.bounds
    rgb, valid, tr = ti.read(L, T, R - L, T - B, 0.2)
    src = np.concatenate([rgb.transpose(2, 0, 1), (valid * 255).astype(np.uint8)[None]], 0)
    src_tr = from_origin(tr[0], tr[1], tr[2], tr[2])
    H, W = valid.shape
    dst_tr, dw, dh = calculate_default_transform("EPSG:32635", "EPSG:3857", W, H, *rasterio.transform.array_bounds(H, W, src_tr), resolution=res)
    dst = np.zeros((4, dh, dw), np.uint8)
    reproject(src, dst, src_transform=src_tr, src_crs="EPSG:32635", dst_transform=dst_tr, dst_crs="EPSG:3857",
              resampling=Resampling.bilinear)
    b = rasterio.transform.array_bounds(dh, dw, dst_tr)
    ll = transform_bounds("EPSG:3857", "EPSG:4326", *b)
    Image.fromarray(dst[:3].transpose(1, 2, 0)).save(out_jpg, quality=82)
    return [[ll[1], ll[0]], [ll[3], ll[2]]]


def build(out_dir, web_dir, tiles_dir):
    d = os.path.join(web_dir, "data")
    os.makedirs(d, exist_ok=True)
    names = ["blocks", "vineyard", "row", "interrow_area", "waste", "inspection", "route", "row_gaps", "route_lawnmower"]
    for n in names:
        p = os.path.join(out_dir, f"{n}.geojson")
        if not os.path.exists(p):
            continue
        g = gpd.read_file(p)
        if n in ("vineyard", "interrow_area", "blocks"):
            g["area_m2"] = g.geometry.area.round(2)
        if n == "row":
            g["length_m"] = g.geometry.length.round(2)
        g = g.to_crs("EPSG:4326")
        g.to_file(os.path.join(d, f"{n}.geojson"), driver="GeoJSON", layer_options={"COORDINATE_PRECISION": 7})
    if os.path.exists(os.path.join(out_dir, "start_used.geojson")):
        gpd.read_file(os.path.join(out_dir, "start_used.geojson")).to_crs("EPSG:4326").to_file(
            os.path.join(d, "start.geojson"), driver="GeoJSON")
    m = pd.read_csv(os.path.join(out_dir, "measurements.csv"))
    m.to_csv(os.path.join(d, "measurements.csv"), index=False)
    ti = TileIndex(tiles_dir)
    bounds = ortho_image(ti, os.path.join(d, "ortho.jpg"))
    json.dump({"ortho_bounds": bounds, "n_tiles": len(ti.tiles)}, open(os.path.join(d, "meta.json"), "w"))
    print("web data written to", d)


if __name__ == "__main__":
    import sys
    build(sys.argv[1] if len(sys.argv) > 1 else "output", "web", sys.argv[2] if len(sys.argv) > 2 else "data/tiles")


def build_generic(out_dir, src, max_px=6000):
    """Static viewer for any run of `python -m vine run`: OUT/web/index.html + OUT/web/data/*.
    Serve with:  python -m http.server 8000 -d OUT/web"""
    web = os.path.join(out_dir, "web")
    d = os.path.join(web, "data")
    os.makedirs(d, exist_ok=True)
    shutil.copy(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "index.html"),
                os.path.join(web, "index.html"))
    for f in os.listdir(os.path.join(out_dir, "wgs84")):
        g = gpd.read_file(os.path.join(out_dir, "wgs84", f))
        if f.startswith(("vineyard", "interrow_area", "blocks")):
            g["area_m2"] = g.to_crs(src.crs).geometry.area.round(2)
        g.to_file(os.path.join(d, f), driver="GeoJSON", layer_options={"COORDINATE_PRECISION": 7})
    if os.path.exists(os.path.join(out_dir, "measurements.csv")):
        shutil.copy(os.path.join(out_dir, "measurements.csv"), os.path.join(d, "measurements.csv"))
    L, B, R, T = src.bounds
    res = max(getattr(src, "gsd", 0.05), max(R - L, T - B) / max_px)
    rgb, valid, tr = src.read(L, T, R - L, T - B, res)
    H, W = valid.shape
    arr = np.concatenate([rgb.transpose(2, 0, 1), (valid * 255).astype(np.uint8)[None]], 0)
    src_tr = from_origin(tr[0], tr[1], res, res)
    dst_tr, dw, dh = calculate_default_transform(src.crs, "EPSG:3857", W, H,
                                                 *rasterio.transform.array_bounds(H, W, src_tr))
    dst = np.zeros((4, dh, dw), np.uint8)
    reproject(arr, dst, src_transform=src_tr, src_crs=src.crs, dst_transform=dst_tr, dst_crs="EPSG:3857",
              resampling=Resampling.bilinear)
    Image.fromarray(dst[:3].transpose(1, 2, 0)).save(os.path.join(d, "ortho.jpg"), quality=82)
    b = rasterio.transform.array_bounds(dh, dw, dst_tr)
    ll = transform_bounds("EPSG:3857", "EPSG:4326", *b)
    json.dump({"ortho_bounds": [[ll[1], ll[0]], [ll[3], ll[2]]], "n_tiles": len(src.tiles)},
              open(os.path.join(d, "meta.json"), "w"))
    print("web viewer:", web, "->  python -m http.server 8000 -d", web)
