"""geotech-model: detection, measurements and farmer / auditor routes behind the JSON contract.

    uvicorn service.app:app --host 0.0.0.0 --port 9090
Volumes: /data/tiles (read-only GeoTIFFs), /data/route (start / passages / forbidden), /data/cache (writable).
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Body, FastAPI, HTTPException                       # noqa: E402
from fastapi.responses import JSONResponse                            # noqa: E402

from service import store                                             # noqa: E402

app = FastAPI(title="geotech-model", version=store.MODEL_VERSION)
_READY = {"ok": False}
_RUN = threading.Semaphore(int(os.environ.get("MODEL_WORKERS", "1")))   # one heavy job at a time (RAM budget)


@app.on_event("startup")
def _warm():
    def w():
        import vine.rows as VR
        from vine.model import load
        VR.CLF_PATH = ""
        VR.CANOPY_MODE = "cive"
        mp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model", "vine_model_v1.json")
        if os.path.exists(mp):
            load(mp)
        VR.CANOPY_MODE = "cive"
        import vine.candidates, vine.assemble, vine.waste, vine.route_opt   # noqa: F401,E401
        _READY["ok"] = True
    threading.Thread(target=w, daemon=True).start()


def _need_ready():
    if not _READY["ok"]:
        raise HTTPException(503, "model is loading")


@app.get("/health")
def health():
    return {"status": "ok", "model_version": store.MODEL_VERSION, "ready": _READY["ok"]}


@app.post("/v1/detect")
def detect(body: dict = Body(...)):
    _need_ready()
    tiles = body.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise HTTPException(422, "tiles: non-empty list of tile names required")
    force = bool(body.get("force", False))
    t0 = time.time()
    unknown = [t for t in tiles if store._tile_path(t) is None]
    if unknown and len(unknown) == len(tiles):
        raise HTTPException(404, f"unknown tile(s): {', '.join(unknown)}")
    errors = {t: "not found in /data/tiles" for t in unknown}
    with _RUN:
        for t in tiles:
            if t in errors:
                continue
            try:
                store.detect_tile(t, force=force)
            except Exception as e:
                errors[t] = f"unreadable or failed: {e}"
    out = {}
    for t in tiles:
        if t not in errors:
            out[os.path.basename(t)] = store.tile_payload(t)
    return {"model_version": store.MODEL_VERSION, "tiles": out, "errors": errors, "elapsed_s": round(time.time() - t0, 1)}


@app.delete("/v1/tiles/{tile_name}")
def delete(tile_name: str):
    if not store.delete_tile(tile_name):
        raise HTTPException(404, f"unknown tile: {tile_name}")
    return {"deleted": tile_name}


@app.post("/v1/measurements")
def measurements(body: dict = Body(default={})):
    _need_ready()
    return store.measurements(body.get("tiles"), body.get("area_world"))


@app.post("/v1/route")
def route(body: dict = Body(default={})):
    _need_ready()
    role = body.get("role", "farmer")
    if role not in ("farmer", "auditor"):
        raise HTTPException(422, "role must be 'farmer' or 'auditor'")
    sw = body.get("start_world")
    if sw is not None and (not isinstance(sw, list) or len(sw) != 2):
        raise HTTPException(422, "start_world must be [x, y] in EPSG:32635")
    with _RUN:
        return store.route(role, body.get("tiles"), body.get("area_world"), sw,
                           solve_s=float(os.environ.get("ROUTE_SOLVE_S", "30")))


@app.exception_handler(FileNotFoundError)
def _nf(_, e):
    return JSONResponse(status_code=404, content={"detail": f"unknown tile: {e}"})
