"""Real-data endpoints: the organizer-supplied route infrastructure
(study area / passages / forbidden zones / start point) and the example
tiles with their real, Marcaj-format (CVAT for images 1.1) annotations.

Everything here comes from the actual challenge assets — no synthetic
geometry. See app/data/real_site.py and app/services/cvat_import.py.
"""

import csv
import io
import json
import os

import rasterio
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.data import real_site
from app.schemas.geo import CustomBlockCreate, CustomRouteRequest, FeatureCollection
from app.services import classical_cv, cvat_import, georef, real_metrics, tile_catalog
from app.services import custom_blocks as custom_blocks_svc
from app.services import measurements as measurements_svc
from app.services import real_route as real_route_svc

router = APIRouter(prefix="/api/site", tags=["site"])

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
    doing (route target explosion, over-segmented canopies, etc.)."""
    global _annotated_cache
    if _annotated_cache is None:
        _annotated_cache = (
            cvat_import.load_merged() if _cv_fallback_enabled() else cvat_import.load_examples()
        )
    return _annotated_cache


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


MAX_UPLOAD_FILES = 5
MAX_UPLOAD_MB = 50


@router.post("/upload")
async def upload_tiles(files: list[UploadFile] = File(...)) -> dict:
    """Operator-supplied GeoTIFF tiles: saved next to the 311 supplied ones,
    detected on the spot with the same classical-CV fallback used for those
    (see app/services/classical_cv.py — no route, since a walking route
    needs passages/forbidden zones this field doesn't have). Georeferencing
    is mandatory: a tile with no CRS can't be placed on the map or measured
    in real units, so it's rejected rather than accepted in a degraded mode.
    """
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=422, detail=f"Max {MAX_UPLOAD_FILES} files per upload")

    results = []
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

        pixel_result = classical_cv.process_tile(dest)
        tile_data = cvat_import.from_pixel_detection(name, pixel_result, source="uploaded")
        _tiles()[name] = tile_data  # merge into the live cache _tiles() already returned

        results.append(
            {
                "filename": name,
                "ok": True,
                "canopies": len(tile_data["canopies"]),
                "rows": len(tile_data["rows"]),
                "interrows": len(tile_data["interrows"]),
            }
        )

    return {"results": results}


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
    t = _tiles().get(tile_name, _EMPTY_TILE)
    return {"tile": tile_name, "blocks": real_metrics.compute_tile_metrics(t)}


@router.get("/measurements")
def get_measurements() -> dict:
    """Aggregated across every currently loaded tile (all pieces of a row or
    block merged by vineyard_id/row_id) — this is the computation behind the
    submission's measurements.csv, exposed as JSON for the UI."""
    return measurements_svc.compute_measurements(_tiles())


@router.get("/measurements.csv")
def get_measurements_csv() -> Response:
    m = measurements_svc.compute_measurements(_tiles())
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
def get_real_route() -> dict:
    """The actual walking route over real passages/forbidden/inter-row
    geometry, to real derived targets (disrupted rows + waste). Returns
    route=None with an explanatory message when there are no targets yet in
    the currently loaded annotations — see app/services/targets.py."""
    return real_route_svc.compute_real_route(_tiles())


@router.post("/route/custom")
def compute_custom_route(body: CustomRouteRequest) -> dict:
    """Operator-controlled route: a custom start point, and/or a scope
    (specific tiles, or a hand-drawn area) restricting which real targets
    the tour visits. Same pathfinding engine as the default /route — this
    is not a separate demo path."""
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
def get_route_geojson(tiles: str | None = None) -> Response:
    """The submission artifact: one LineString in EPSG:32635 with a
    length_m property, starting and ending at the real start point.

    Optional `?tiles=a.tif,b.tif` scopes it, same as POST /route/custom —
    useful since a single tour across every derived target on the whole
    site isn't a realistic route (see MAX_TARGETS_FOR_ROUTE)."""
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
