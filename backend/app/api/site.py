"""Real-data endpoints: the organizer-supplied route infrastructure
(study area / passages / forbidden zones / start point) and the example
tiles with their real, Marcaj-format (CVAT for images 1.1) annotations.

Everything here comes from the actual challenge assets — no synthetic
geometry. See app/data/real_site.py and app/services/cvat_import.py.
"""

import csv
import io
import json
import logging
import os
import threading
import uuid

import rasterio
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.data import real_site
from app.schemas.geo import CustomBlockCreate, CustomRouteRequest, FeatureCollection
from app.services import classical_cv, cvat_export, cvat_import, georef, real_metrics, tile_catalog
from app.services import custom_blocks as custom_blocks_svc
from app.services import model_client
from app.services import measurements as measurements_svc
from app.services import real_route as real_route_svc

router = APIRouter(prefix="/api/site", tags=["site"])
log = logging.getLogger(__name__)

_annotated_cache: dict | None = None

_EMPTY_TILE = {"canopies": [], "rows": [], "interrows": [], "waste": []}


def _cv_fallback_enabled() -> bool:
    return os.environ.get("CV_FALLBACK", "true").strip().lower() not in ("0", "false", "no")


def _tiles() -> dict:
    """Tiles with real Marcaj-format annotations. Ground truth (the 2
    example tiles) is always included; the classical-CV fallback for the
    other 309 (see scripts/classical_pre_annotate.py) is on by default and
    can be switched off with `CV_FALLBACK=false` — e.g. to fall back to just
    the known-good 2 tiles if the CV output is too noisy for what you're
    doing (route target explosion, over-segmented canopies, etc.).

    The model service's detections take over entirely when it is up."""
    model = _model_tiles()
    if model is not None:
        return model
    global _annotated_cache
    if _annotated_cache is None:
        _annotated_cache = (
            cvat_import.load_merged() if _cv_fallback_enabled() else cvat_import.load_examples()
        )
        _annotated_cache.update(cvat_import.load_uploaded())
    return _annotated_cache


# --- model service (model-service/, see services/model_client.py) ---------
# When it is up it owns detection, measurements and routes; everything
# below falls back to the in-process classical CV / our own route code
# whenever `_model_tiles()` returns None.

_model_cache: dict | None = None  # tile -> internal per-tile data, from /v1/detect
# Whole-site routes per role (farmer: 196 targets, ~400 s; auditor: ~130 s),
# planned offline with the model's own planner — past a request's timeout
# and the service's 80-target HTTP cap. Served as the unscoped default
# route; the farmer one is the submitted route.geojson (README "Submission").
SITE_ROUTE_DIR = real_site.ASSETS_DIR.parent

# Routes take the service ~45-60 s — keep each answer until tiles change.
_route_cache: dict[str, dict] = {}
_route_lock = threading.Lock()


def _model_tiles() -> dict | None:
    """Model detections for every catalog tile, converted to our local-plane
    format, or None when the service isn't available. The service caches
    per-tile results itself, so asking for all of them is cheap after the
    first time; IDs are re-stitched site-wide there on every detect/delete,
    hence one cache for all tiles, dropped whenever the tile set changes."""
    global _model_cache
    if not model_client.available():
        return None
    if _model_cache is None:
        names = list(tile_catalog.get_catalog())
        try:
            payload = model_client.detect(names)["tiles"] if names else {}
        except model_client.ModelError as exc:
            log.warning("Model detect failed, using classical CV: %s", exc.detail)
            return None
        _model_cache = {
            name: cvat_import.from_pixel_detection(name, tile, source="model")
            for name, tile in payload.items()
        }
    return _model_cache


def _invalidate_model() -> None:
    global _model_cache
    _model_cache = None
    _route_cache.clear()


def _route_from_model(result: dict) -> dict:
    """/v1/route answer (EPSG:32635) -> the shape the UI already renders."""
    targets = [
        {**{k: v for k, v in t.items() if k != "point_world"}, "point": list(real_site.to_local(*t["point_world"]))}
        for t in result["targets"]
    ]
    route = result.get("route")
    if route is not None:
        route = {
            **route,
            "polyline_local": [list(real_site.to_local(x, y)) for x, y in route["polyline_world"]],
        }
    return {"targets": targets, "route": route, "message": result.get("message"), "role": result.get("role")}


def _model_route(
    role: str,
    tiles: list[str] | None = None,
    area_local: list[list[float]] | None = None,
    start_local: list[float] | None = None,
) -> dict | None:
    """A cached model-service route, or None to fall back to ours."""
    if _model_tiles() is None:
        return None
    site_route = SITE_ROUTE_DIR / f"site_route_{role}.json"
    if not (tiles or area_local or start_local) and site_route.exists():
        return _route_from_model(json.loads(site_route.read_text(encoding="utf-8")))
    key = json.dumps([role, tiles, area_local, start_local])
    with _route_lock:
        if key in _route_cache:
            return _route_cache[key]
        try:
            result = model_client.route(
                role,
                tiles=tiles,
                area_world=[list(real_site.to_world(x, y)) for x, y in area_local] if area_local else None,
                start_world=list(real_site.to_world(*start_local)) if start_local else None,
            )
        except model_client.ModelError as exc:
            if exc.status is not None and exc.status < 500:
                raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
            log.warning("Model route failed, using our own: %s", exc.detail)
            return None
        _route_cache[key] = _route_from_model(result)
        return _route_cache[key]


def _model_measurements(tiles: list[str] | None = None) -> dict | None:
    if _model_tiles() is None:
        return None
    try:
        return model_client.measurements(tiles=tiles)
    except model_client.ModelError as exc:
        log.warning("Model measurements failed, using our own: %s", exc.detail)
        return None


def _require_real_tile(tile_name: str) -> None:
    """Validates against the full 311-tile catalog, not just the annotated
    subset — any supplied tile can be viewed, just not all have layers yet."""
    if not tile_catalog.tile_exists(tile_name):
        raise HTTPException(status_code=404, detail=f"Unknown tile '{tile_name}'")


@router.get("/route-infra")
def get_route_infra() -> dict:
    return {
        "study_area": real_site.get_study_area_local(),
        "passages": real_site.get_passages_local(),
        "forbidden": real_site.get_forbidden_local(),
        "start": real_site.get_start_local(),
        "extent": list(real_site.get_extent_local()),
    }


@router.get("/tiles")
def list_tiles() -> list[dict]:
    annotated = _tiles()
    return [
        {
            "tile": name,
            "bounds": list(bounds),
            "vineyard_ids": sorted(
                {c.get("vineyard_id") for c in annotated[name]["canopies"]}
            )
            if name in annotated
            else [],
            "annotated": name in annotated,
            "source": annotated[name]["source"] if name in annotated else None,
        }
        for name, bounds in tile_catalog.get_catalog().items()
    ]


MAX_UPLOAD_FILES = 10
MAX_UPLOAD_MB = 50


@router.post("/upload")
async def upload_tiles(files: list[UploadFile] = File(...)) -> dict:
    """Operator-supplied GeoTIFF tiles: saved next to the 311 supplied ones
    and detected on the spot — by the model service when it is up (one
    /v1/detect call for the whole request), otherwise with the in-process
    classical CV (app/services/classical_cv.py). Georeferencing is
    mandatory: a tile with no CRS can't be placed on the map or measured in
    real units, so it's rejected rather than accepted in a degraded mode.
    """
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=422, detail=f"Max {MAX_UPLOAD_FILES} files per upload")

    results = []
    batch_pixel_results: dict[str, dict] = {}  # this request's tiles only — for its own CVAT XML
    saved: list[str] = []
    for f in files:
        name = f.filename or "unnamed.tif"
        if not name.lower().endswith((".tif", ".tiff")):
            results.append({"filename": name, "ok": False, "error": "Not a .tif/.tiff file"})
            continue

        content = await f.read()
        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            results.append(
                {"filename": name, "ok": False, "error": f"Exceeds {MAX_UPLOAD_MB}MB limit"}
            )
            continue

        dest = tile_catalog.TILES_DIR / name
        dest.write_bytes(content)

        try:
            with rasterio.open(dest) as ds:
                has_crs = ds.crs is not None
        except Exception as exc:  # noqa: BLE001 — surfaced to the operator, not a bug
            dest.unlink(missing_ok=True)
            results.append({"filename": name, "ok": False, "error": f"Not a valid GeoTIFF: {exc}"})
            continue

        if not has_crs:
            dest.unlink(missing_ok=True)
            results.append(
                {
                    "filename": name,
                    "ok": False,
                    "error": "No CRS/geotransform in this file — can't place it on the map "
                    "or measure it in real units.",
                }
            )
            continue

        tile_catalog.invalidate()
        georef._transform_cache.pop(name, None)
        georef._bounds_cache.pop(name, None)
        (georef.PNG_CACHE_DIR / f"{name}.jpg").unlink(missing_ok=True)
        saved.append(name)

    model_payload: dict[str, dict] = {}
    if saved and model_client.available():
        try:
            response = model_client.detect(saved, force=True)
            model_payload = response["tiles"]
            for name, err in response.get("errors", {}).items():
                log.warning("Model couldn't detect %s (%s), using classical CV", name, err)
        except model_client.ModelError as exc:
            log.warning("Model detect failed, using classical CV: %s", exc.detail)

    for name in saved:
        if name in model_payload:
            pixel_result, source = model_payload[name], "model"
        else:
            pixel_result, source = classical_cv.process_tile(tile_catalog.TILES_DIR / name), "uploaded"
        batch_pixel_results[name] = pixel_result

        tile_data = cvat_import.from_pixel_detection(name, pixel_result, source=source)
        # Also kept on disk so the tile still has layers if the model service
        # is down later (classical-CV fallback path reads these back).
        cvat_import.save_uploaded(name, tile_data)
        if _annotated_cache is not None:
            _annotated_cache[name] = tile_data

        results.append(
            {
                "filename": name,
                "ok": True,
                "canopies": len(tile_data["canopies"]),
                "rows": len(tile_data["rows"]),
                "interrows": len(tile_data["interrows"]),
            }
        )

    if saved:
        _invalidate_model()  # new tiles re-stitch IDs site-wide on the model side

    annotation_xml_url = None
    if batch_pixel_results:
        # One CVAT XML for exactly the tile(s) uploaded in THIS request — not
        # the whole catalog — so it can be downloaded and re-imported (e.g.
        # into a real Marcaj project) as a self-contained pre-annotation set.
        xml_name = f"upload_{uuid.uuid4().hex[:12]}.xml"
        cvat_export.write_cvat_xml(batch_pixel_results, cvat_import.UPLOADED_DIR / xml_name)
        annotation_xml_url = f"/api/site/uploaded-annotations/{xml_name}"

    return {"results": results, "annotation_xml_url": annotation_xml_url}


@router.get("/uploaded-annotations/{filename}")
def get_uploaded_annotation_xml(filename: str) -> Response:
    """Downloads one upload request's CVAT XML (see /upload) — scoped to
    just the tile(s) from that request, named upload_<id>.xml."""
    if "/" in filename or not filename.endswith(".xml"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = cvat_import.UPLOADED_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Unknown annotation export")
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/tiles/{tile_name}/layers")
def get_tile_layers(tile_name: str) -> dict[str, FeatureCollection]:
    _require_real_tile(tile_name)
    t = _tiles().get(tile_name, _EMPTY_TILE)
    return {
        "canopies": FeatureCollection(
            features=[
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [c["polygon"]]},
                    "properties": {k: v for k, v in c.items() if k != "polygon"},
                }
                for c in t["canopies"]
            ]
        ),
        "rows": FeatureCollection(
            features=[
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": r["line"]},
                    "properties": {k: v for k, v in r.items() if k != "line"},
                }
                for r in t["rows"]
            ]
        ),
        "interrows": FeatureCollection(
            features=[
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [i["polygon"]]},
                    "properties": {k: v for k, v in i.items() if k != "polygon"},
                }
                for i in t["interrows"]
            ]
        ),
        "waste": FeatureCollection(
            features=[
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [w["bbox"]]},
                    "properties": {k: v for k, v in w.items() if k != "bbox"},
                }
                for w in t["waste"]
            ]
        ),
    }


@router.get("/tiles/{tile_name}/metrics")
def get_tile_metrics(tile_name: str) -> dict:
    _require_real_tile(tile_name)
    m = _model_measurements([tile_name])
    if m is not None:
        return {"tile": tile_name, "blocks": m["blocks"]}
    t = _tiles().get(tile_name, _EMPTY_TILE)
    return {"tile": tile_name, "blocks": real_metrics.compute_tile_metrics(t)}


@router.get("/measurements")
def get_measurements() -> dict:
    """Aggregated across every currently loaded tile (all pieces of a row or
    block merged by vineyard_id/row_id) — this is the computation behind the
    submission's measurements.csv, exposed as JSON for the UI."""
    return _model_measurements() or measurements_svc.compute_measurements(_tiles())


@router.get("/measurements.csv")
def get_measurements_csv() -> Response:
    m = _model_measurements() or measurements_svc.compute_measurements(_tiles())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=measurements_svc.CSV_FIELDS)
    writer.writeheader()
    writer.writerows(measurements_svc.to_csv_rows(m))
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=measurements.csv"},
    )


@router.get("/route")
def get_real_route(role: Literal["farmer", "auditor"] = "farmer") -> dict:
    """The walking route for `role` — planned by the model service when it
    is up (farmer: row gaps + waste; auditor: verification sample), else our
    own route to derived targets (same for both roles). Returns route=None
    with an explanatory message when there's nothing to route."""
    model = _model_route(role)
    if model is not None:
        return model
    return real_route_svc.compute_real_route(_tiles())


@router.post("/route/custom")
def compute_custom_route(body: CustomRouteRequest) -> dict:
    """Operator-controlled route: a custom start point, and/or a scope
    (specific tiles, or a hand-drawn area) restricting which real targets
    the tour visits. Same engine as the default /route — this is not a
    separate demo path."""
    model = _model_route(body.role, body.tiles, body.area, body.start)
    if model is not None:
        return model
    all_tiles = _tiles()
    scoped = (
        {name: t for name, t in all_tiles.items() if name in body.tiles}
        if body.tiles is not None
        else all_tiles
    )
    start_point = tuple(body.start) if body.start is not None else None
    return real_route_svc.compute_real_route(
        scoped, start_point=start_point, area_polygon=body.area
    )


@router.get("/route.geojson")
def get_route_geojson(
    tiles: str | None = None, role: Literal["farmer", "auditor"] = "farmer"
) -> Response:
    """The submission artifact: one LineString in EPSG:32635 with a
    length_m property, starting and ending at the real start point.

    Optional `?tiles=a.tif,b.tif` scopes it, same as POST /route/custom —
    useful since a single tour across every derived target on the whole
    site isn't a realistic route (see MAX_TARGETS_FOR_ROUTE)."""
    result = _model_route(role, tiles.split(",") if tiles else None)
    if result is None:
        all_tiles = _tiles()
        scoped = (
            {name: t for name, t in all_tiles.items() if name in tiles.split(",")}
            if tiles
            else all_tiles
        )
        result = real_route_svc.compute_real_route(scoped)
    route = result["route"]
    if route is None:
        raise HTTPException(status_code=404, detail=result["message"])

    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": route["polyline_world"]},
                "properties": {"length_m": route["length_m"], "order": route["order"]},
            }
        ],
    }
    return Response(
        content=json.dumps(feature_collection, indent=2),
        media_type="application/geo+json",
        headers={"Content-Disposition": "attachment; filename=route.geojson"},
    )


@router.get("/custom-blocks")
def list_custom_blocks() -> list[dict]:
    """Manually-drawn vineyard block boundaries (see services/custom_blocks.py)."""
    return custom_blocks_svc.list_blocks()


@router.post("/custom-blocks")
def create_custom_block(body: CustomBlockCreate) -> dict:
    if len(body.polygon) < 3:
        raise HTTPException(status_code=422, detail="Polygon needs at least 3 points")
    ring = body.polygon if body.polygon[0] == body.polygon[-1] else [*body.polygon, body.polygon[0]]
    return custom_blocks_svc.add_block(body.vineyard_id, ring)


@router.delete("/custom-blocks/{block_id}")
def delete_custom_block(block_id: str) -> dict:
    ok = custom_blocks_svc.delete_block(block_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown custom block '{block_id}'")
    return {"deleted": block_id}


@router.delete("/tiles/{tile_name}")
def delete_tile(tile_name: str) -> dict:
    """Removes a tile's GeoTIFF plus everything derived from it (saved
    upload detections, cached JPEG, in-memory caches). Validated against the
    catalog first, so tile_name is always a real file in TILES_DIR."""
    _require_real_tile(tile_name)
    (tile_catalog.TILES_DIR / tile_name).unlink(missing_ok=True)
    (cvat_import.UPLOADED_DIR / f"{tile_name}.json").unlink(missing_ok=True)
    (georef.PNG_CACHE_DIR / f"{tile_name}.jpg").unlink(missing_ok=True)
    georef._transform_cache.pop(tile_name, None)
    georef._bounds_cache.pop(tile_name, None)
    tile_catalog.invalidate()
    if _annotated_cache is not None:
        _annotated_cache.pop(tile_name, None)
    if model_client.MODEL_URL:
        try:
            model_client.delete_tile(tile_name)
        except model_client.ModelError as exc:
            log.warning("Model service didn't drop %s: %s", tile_name, exc.detail)
    _invalidate_model()
    return {"deleted": tile_name}


@router.get("/tiles/{tile_name}/image.jpg")
def get_tile_image(tile_name: str) -> Response:
    _require_real_tile(tile_name)
    try:
        jpeg_bytes = georef.tile_to_jpeg(tile_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tile image not found") from exc
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
