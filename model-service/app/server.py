"""Web server: the Sireț3 result map (/) and the interactive model test page (/test).

The test page lets a user upload any vineyard image (GeoTIFF, or JPG/PNG + ground resolution),
draw the area to check, click the start point, and run the full pipeline: canopies, rows, inter-rows,
waste, inspection points, measurements, the walking route and turn-by-turn instructions.

run:  python app/server.py            -> http://localhost:8000
"""
import io
import json
import os
import sys
import threading
import time
import traceback
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import geopandas as gpd                                      # noqa: E402
import numpy as np                                           # noqa: E402
from flask import Flask, jsonify, request, send_file, send_from_directory, abort   # noqa: E402
from PIL import Image                                        # noqa: E402
from shapely.affinity import affine_transform               # noqa: E402
from shapely.geometry import Point, Polygon, mapping        # noqa: E402
from shapely.ops import unary_union                          # noqa: E402

from vine.source import ImageSource                          # noqa: E402

WEB = os.path.join(ROOT, "web")
UP = os.path.join(ROOT, "work", "uploads")
os.makedirs(UP, exist_ok=True)
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3
IMAGES, JOBS = {}, {}
MAX_JOBS = int(os.environ.get("VINE_MAX_JOBS", "1"))       # concurrent model runs (CPU / RAM bound)
_SLOTS = threading.BoundedSemaphore(MAX_JOBS)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok", running=sum(1 for j in JOBS.values() if j["status"] == "running"), max_jobs=MAX_JOBS)


# ------------------------------------------------------------------ static pages
@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/test")
def test_page():
    return send_from_directory(WEB, "test.html")


@app.get("/roles")
def roles_page():
    return send_from_directory(WEB, "roles.html")


@app.get("/bench")
def bench_page():
    return send_from_directory(WEB, "bench.html")


@app.get("/bench/<path:p>")
def bench_files(p):
    return send_from_directory(os.path.join(WEB, "bench"), p)


@app.get("/data/<path:p>")
def data(p):
    return send_from_directory(os.path.join(WEB, "data"), p)


# ------------------------------------------------------------------ images
def _register(path, gsd=None):
    iid = os.path.basename(os.path.dirname(path))
    src = ImageSource(path, gsd=gsd)
    prev, scale = src.preview()
    Image.fromarray(prev).save(os.path.join(os.path.dirname(path), "preview.jpg"), quality=88)
    IMAGES[iid] = dict(path=path, gsd=gsd, scale=scale)
    return dict(id=iid, kind=src.kind, width=src.width, height=src.height, gsd_m=round(src.gsd, 4),
                preview_w=prev.shape[1], preview_h=prev.shape[0], scale=scale,
                crs=str(src.crs) if src.crs else "local metric (no georeference)",
                size_m=[round(src.width * src.gsd, 1), round(src.height * src.gsd, 1)])


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f:
        abort(400, "no file")
    iid = uuid.uuid4().hex[:10]
    d = os.path.join(UP, iid)
    os.makedirs(d)
    name = os.path.basename(f.filename) or "image.tif"
    path = os.path.join(d, name)
    f.save(path)
    gsd = request.form.get("gsd", type=float)
    if gsd:
        open(os.path.join(d, "gsd.txt"), "w").write(str(gsd))
    try:
        return jsonify(_register(path, gsd))
    except Exception as e:
        return jsonify(error=f"cannot read image: {e}"), 400


@app.post("/api/sample")
def sample():
    """Cut a GeoTIFF from the Sireț3 tiles (default: around the organiser start point)."""
    import rasterio
    from rasterio.transform import from_origin
    from vine.common import TileIndex
    body = request.get_json(silent=True) or {}
    r0, c0, n = int(body.get("row", 18)), int(body.get("col", 10)), int(body.get("n", 4))
    ex = os.path.join(ROOT, "examples", "siret3_sample.tif")
    if not os.path.isdir(os.path.join(ROOT, "data", "tiles")) and os.path.exists(ex):
        import shutil                                    # production package: ships one sample GeoTIFF
        iid = uuid.uuid4().hex[:10]
        os.makedirs(os.path.join(UP, iid))
        path = os.path.join(UP, iid, "siret3_sample.tif")
        shutil.copy(ex, path)
        return jsonify(_register(path))
    ti = TileIndex(os.path.join(ROOT, "data", "tiles"))
    L = ti.x0 + c0 * 51.2
    T = ti.y0 - r0 * 51.2
    rgb, valid, tr = ti.read(L, T, n * 51.2, n * 51.2, 0.05)
    iid = uuid.uuid4().hex[:10]
    d = os.path.join(UP, iid)
    os.makedirs(d)
    path = os.path.join(d, f"siret3_sample_r{r0:03d}_c{c0:03d}_{n}x{n}.tif")
    with rasterio.open(path, "w", driver="GTiff", width=rgb.shape[1], height=rgb.shape[0], count=3,
                       dtype="uint8", crs="EPSG:32635", transform=from_origin(tr[0], tr[1], 0.05, 0.05),
                       compress="jpeg", photometric="ycbcr", tiled=True) as dst:
        dst.write(rgb.transpose(2, 0, 1))
    return jsonify(_register(path))


@app.get("/api/image/<iid>/preview.jpg")
def preview(iid):
    return send_file(os.path.join(UP, iid, "preview.jpg"))


# ------------------------------------------------------------------ model run
def _to_px(geom, src, scale):
    """world -> preview pixel coords, emitted as (x, -y) so Leaflet CRS.Simple shows it upright."""
    inv = ~src.transform
    a, b, c, d, e, f = inv.a, inv.b, inv.c, inv.d, inv.e, inv.f
    g = affine_transform(geom, [a / scale, b / scale, -d / scale, -e / scale, c / scale, -f / scale])
    return g


def _fc(gdf, src, scale, keep):
    feats = []
    if gdf is None or not len(gdf):
        return dict(type="FeatureCollection", features=[])
    for r in gdf.to_dict("records"):
        g = _to_px(r["geometry"], src, scale)
        props = {k: (v if not isinstance(v, (np.floating, np.integer)) else v.item()) for k, v in r.items()
                 if k in keep}
        feats.append(dict(type="Feature", geometry=mapping(g), properties=props))
    return dict(type="FeatureCollection", features=feats)


def _run(jid, iid, aoi_px, start_px, opts):
    with _SLOTS:
        _run_job(jid, iid, aoi_px, start_px, opts or {})


def _run_job(jid, iid, aoi_px, start_px, opts):
    job = JOBS[jid]

    def log(*a):
        job["log"].append(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a))

    try:
        from vine.candidates import find_candidates
        from vine.rows import process_region
        from vine.assemble import assemble, build_tables, measurements
        from vine.waste import detect_waste
        from vine.roles import write_roles
        from vine.common import CRS
        t0 = time.time()
        im = IMAGES[iid]
        src0 = ImageSource(im["path"], gsd=im["gsd"])
        scale = im["scale"]
        tr = src0.transform
        to_world = lambda px, py: tr * (px * scale, py * scale)          # noqa: E731
        aoi = Polygon([to_world(x, y) for x, y in aoi_px]).buffer(0)
        start = Point(*to_world(*start_px))
        log(f"image: {src0.kind}, {src0.gsd * 100:.1f} cm/px; area of interest {aoi.area / 1e4:.2f} ha")
        if src0.gsd > 0.08:
            log("WARNING: resolution coarser than 8 cm/px - individual vines may not be resolved")
        src = ImageSource(im["path"], gsd=im["gsd"], aoi=aoi)
        crs = src.crs.to_string() if src.crs else CRS

        cands, _ = find_candidates(src, log)
        results = []
        for i, c in enumerate(cands):
            log(f"analysing candidate region {i + 1}/{len(cands)} ({c['geometry'].area:.0f} m2)")
            results.append(process_region(src, c["geometry"], c["freq_angle"], log))
        blocks = assemble(results)
        log(f"{len(blocks)} vineyard block(s), {sum(len(b['rows']) for b in blocks)} rows")
        uw = os.path.join(ROOT, "model", "canopy_unet.pth")
        if blocks and os.environ.get("CANOPY") == "unet" and os.path.exists(uw):
            from vine.canopy_unet import apply_unet
            apply_unet(blocks, src, uw, log)
        canopy, rows, interrow, inspect = build_tables(blocks)
        for r in rows:
            r.pop("gap_lines", None)
        meas = measurements(blocks, canopy, rows, interrow) if blocks else None
        G = lambda recs: gpd.GeoDataFrame(recs, geometry="geometry", crs=crs) if recs else \
            gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)          # noqa: E731
        waste = []
        if blocks:
            zone = unary_union([b["geometry"] for b in blocks]).buffer(20).intersection(aoi)
            waste = detect_waste(src, zone, [r["geometry"] for r in rows], log)
            for i, w in enumerate(waste, 1):
                d = sorted((b["geometry"].distance(w["geometry"]), b["vineyard_id"]) for b in blocks)
                w["vineyard_id"] = d[0][1] if d[0][0] <= 10 else ""
                w["waste_id"] = f"W{i:04d}"
        layers = dict(vineyard=G(canopy), row=G(rows), interrow_area=G(interrow), inspection=G(inspect),
                      waste=G([dict(waste_id=w["waste_id"], vineyard_id=w["vineyard_id"], kind=w["kind"],
                                    geometry=w["geometry"]) for w in waste]),
                      blocks=G([dict(vineyard_id=b["vineyard_id"], n_rows=len(b["rows"]),
                                     geometry=b["geometry"]) for b in blocks]))
        # route: in test mode every open (non-vineyard) ground inside the AOI counts as an authorised path
        bu = unary_union([b["geometry"] for b in blocks]) if blocks else None
        passages = aoi.difference(bu.buffer(0.3)) if bu is not None else aoi
        # save downloadable results (world coordinates) + the two role routes
        out = os.path.join(UP, iid, "run_" + jid)
        os.makedirs(out, exist_ok=True)
        for k, g in layers.items():
            if len(g):
                g.to_file(os.path.join(out, f"{k}.geojson"), driver="GeoJSON")
        if meas is not None:
            meas.to_csv(os.path.join(out, "measurements.csv"), index=False)
        roles = [r for r in opts.get("roles", ["farmer", "inspector"]) if r in ("farmer", "inspector")]
        res = {}
        if blocks and roles:
            log(f"planning the role routes: {', '.join(roles)} ...")
            res = write_roles(out, layers, start, passages, None, src, log, roles=roles,
                              farmer_lanes=opts.get("farmer_lanes", "all"),
                              turn_radius=float(opts.get("turn_radius") or 0),
                              max_minutes=float(opts["max_minutes"]) if opts.get("max_minutes") else None,
                              solve_s=20, crs=src.crs)
        px = lambda cs: [list(_to_px(Point(*p), src, scale).coords[0]) for p in cs]      # noqa: E731
        py = lambda d: {k: (v.item() if hasattr(v, "item") else v) for k, v in d.items() if k != "geometry"}  # noqa: E731
        role_out = {}
        if "farmer" in res:
            f = res["farmer"]
            role_out["farmer"] = dict(
                route=_fc(f[["geometry"]], src, scale, set()),
                graph=_fc(f.attrs["graph"], src, scale, {"element", "id", "kind", "length_m"}),
                summary={k: v for k, v in py(f.iloc[0].to_dict()).items() if k != "lane_order"},
                steps=[dict(kind=s_["kind"], text=s_["text"], length_m=s_["length_m"], coords=px(s_["coords"]))
                       for s_ in f.attrs["steps"]])
        if "inspector" in res:
            r_ = res["inspector"]
            ins = r_.attrs["instructions"]
            for leg in ins:
                for s_ in leg["steps"]:
                    s_["coords"] = px(s_["coords"])
            role_out["inspector"] = dict(
                route=_fc(r_[["geometry"]], src, scale, set()),
                graph=_fc(r_.attrs["graph"], src, scale, {"element", "id", "kind", "length_m"}),
                summary={k: v for k, v in py(r_.iloc[0].to_dict()).items()
                         if k in ("length_m", "minutes", "n_targets", "reachable_targets", "all_targets",
                                  "outside_passable_pct", "score")},
                instructions=ins, checklist=[py(c) for c in r_.attrs["checklist"].to_dict("records")])
        tot = meas.iloc[0].to_dict() if meas is not None else {}
        job["result"] = dict(
            layers={k: _fc(layers[k], src, scale, keep) for k, keep in dict(
                blocks={"vineyard_id", "n_rows"}, vineyard={"vineyard_id", "row_id"},
                interrow_area={"vineyard_id", "interrow_cover"},
                row={"vineyard_id", "row_id", "row_structure", "length_m"},
                waste={"waste_id", "vineyard_id", "kind"},
                inspection={"inspection_id", "vineyard_id", "row_id", "gap_m"}).items()},
            roles=role_out,
            start=list(_to_px(start, src, scale).coords[0]),
            summary=dict(blocks=len(blocks), rows=len(rows),
                         row_length_m=round(float(tot.get("row_length_m", 0) or 0), 1),
                         canopy_m2=round(float(tot.get("canopy_area_m2", 0) or 0), 1),
                         interrow_m2=round(float(tot.get("interrow_area_m2", 0) or 0), 1),
                         canopies=len(canopy), waste=len(waste), inspection=len(inspect),
                         seconds=round(time.time() - t0, 1)),
            download=f"/api/job/{jid}/files")
        job["status"] = "done"
        log(f"finished in {time.time() - t0:.0f} s")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        log("ERROR: " + str(e))
        traceback.print_exc()


def _recover(iid):
    """Re-register an image uploaded before a server restart."""
    d = os.path.join(UP, os.path.basename(iid or ""))
    if iid and iid not in IMAGES and os.path.isdir(d):
        f = [x for x in os.listdir(d) if x not in ("preview.jpg", "gsd.txt") and not x.startswith("run_")]
        if f:
            meta = os.path.join(d, "gsd.txt")
            _register(os.path.join(d, f[0]), float(open(meta).read()) if os.path.exists(meta) else None)


@app.post("/api/run")
def run():
    b = request.get_json()
    _recover(b.get("id"))
    if b.get("id") not in IMAGES:
        abort(404, "unknown image")
    if len(b.get("aoi") or []) < 3 or not b.get("start"):
        abort(400, "need an area (3+ points) and a start point")
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = dict(status="running", log=[], result=None)
    threading.Thread(target=_run, args=(jid, b["id"], b["aoi"], b["start"], b.get("options")), daemon=True).start()
    return jsonify(job=jid)


@app.get("/api/job/<jid>")
def job(jid):
    j = JOBS.get(jid) or abort(404)
    since = request.args.get("since", 0, type=int)
    return jsonify(status=j["status"], log=j["log"][since:], n=len(j["log"]), error=j.get("error"),
                   result=j["result"] if j["status"] == "done" else None)


@app.get("/api/job/<jid>/files")
def files(jid):
    import zipfile
    for iid in os.listdir(UP):
        d = os.path.join(UP, iid, "run_" + jid)
        if os.path.isdir(d):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for root, _, fs in os.walk(d):
                    for f in fs:
                        z.write(os.path.join(root, f), os.path.relpath(os.path.join(root, f), d))
            buf.seek(0)
            return send_file(buf, download_name=f"vineyard_run_{jid}.zip", as_attachment=True)
    abort(404)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Vineyard web app (map + model test page)")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--dev", action="store_true", help="Flask development server instead of waitress")
    a = ap.parse_args()
    import vine.rows as VR
    VR.CLF_PATH = ""                # training-free by default: CIVE + Otsu canopy (CANOPY=unet to use the U-Net)
    VR.CANOPY_MODE = "cive" if os.environ.get("CANOPY") != "unet" else "auto"
    mp = os.path.join(ROOT, "model", "vine_model_v1.json")
    if os.path.exists(mp):
        from vine.model import load
        print("model loaded:", load(mp)["name"], flush=True)
    if a.dev:
        app.run(host=a.host, port=a.port, threaded=True)
    else:
        from waitress import serve
        print(f"serving on http://{a.host}:{a.port}  (waitress, {MAX_JOBS} concurrent job(s))", flush=True)
        serve(app, host=a.host, port=a.port, threads=8, max_request_body_size=2 * 1024 ** 3)
