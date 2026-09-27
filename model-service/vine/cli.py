"""Production entry point: run the whole pipeline on ANY vineyard orthomosaic.

    python -m vine run INPUT [options]

INPUT   a GeoTIFF (any projection; lat/lon is warped to its local UTM zone), a folder of GeoTIFF
        tiles (any names; Sireț3 challenge tiles are recognised), or a JPG/PNG with --gsd.

Outputs (in --out):
    vineyard.geojson  row.geojson  interrow_area.geojson  waste.geojson  inspection.geojson
    blocks.geojson  row_gaps.geojson  route.geojson  start.geojson     (metric working CRS)
    wgs84/*.geojson                                                    (EPSG:4326 copies for web maps)
    measurements.csv  instructions.txt  summary.json
    annotations.xml   (CVAT 1.1, only for the Sireț3 challenge tiles, or with --cvat)
    web/              (static map viewer: serve with  python -m http.server -d OUT/web)
"""
import argparse
import json
import os
import sys
import time

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box
from shapely.ops import unary_union

from . import common
from .common import set_crs, work_crs


def _log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _read_geom(path, crs):
    g = gpd.read_file(path)
    if g.crs is not None and crs is not None:
        g = g.to_crs(crs)
    return g


def _parse_start(spec, start_crs, crs):
    if spec is None:
        return None
    if os.path.exists(spec):
        return _read_geom(spec, crs).geometry.iloc[0]
    x, y = (float(v) for v in spec.split(","))
    p = gpd.GeoSeries([Point(x, y)], crs=start_crs or crs)
    return p.to_crs(crs).iloc[0] if (start_crs and crs) else p.iloc[0]


def _gdf(recs, crs):
    if not recs:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame(recs, geometry="geometry", crs=crs)


def run(a):
    t0 = time.time()
    timings = {}
    os.makedirs(a.out, exist_ok=True)
    if a.model and os.path.exists(a.model):
        from .model import load
        load(a.model)
    from . import candidates as CAND, rows as VR
    if a.row_spacing:
        lo, hi = (float(v) for v in a.row_spacing.split(","))
        CAND.ROW_PERIOD = (lo, hi)
        VR.ROW_PERIOD = (lo * 0.9, hi * 1.1)
    VR.CLF_PATH = ""
    VR.CANOPY_MODE = "cive" if a.canopy in ("cive", "unet") else a.canopy

    # ---- input
    from .source import open_source
    aoi_geom = None
    src = open_source(a.input, gsd=a.gsd)
    crs = getattr(src, "crs", None)
    if a.aoi:
        aoi_geom = unary_union(_read_geom(a.aoi, crs).geometry)
        src = open_source(a.input, gsd=a.gsd, aoi=aoi_geom)
    set_crs(crs if crs is not None else "LOCAL_CS")
    crs_out = crs if crs is not None else None
    L, B, R, T = src.bounds
    _log(f"input: {src.kind}; {getattr(src, 'gsd', 0) * 100:.1f} cm/px; CRS {crs}; "
         f"extent {R - L:.0f} x {T - B:.0f} m ({(R - L) * (T - B) / 1e4:.1f} ha)")
    if getattr(src, "gsd", 0.03) > 0.08:
        _log("WARNING: resolution coarser than 8 cm/px - single vines may not be resolved")

    passages = forbidden = None
    zone_files = list(a.zones or []) + [f for f in (a.passages, a.forbidden) if f]
    if zone_files:
        from .route import load_zones
        passages, forbidden = load_zones(zone_files)

    # ---- detection
    t = time.time()
    from .candidates import find_candidates
    from .rows import process_region
    from .assemble import assemble, build_tables, measurements
    cands, _ = find_candidates(src, _log)
    results = []
    for i, c in enumerate(cands):
        _log(f"region {i + 1}/{len(cands)} ({c['geometry'].area:.0f} m2)")
        results.append(process_region(src, c["geometry"], c["freq_angle"], _log))
    blocks = assemble(results, passages=passages)
    timings["detection_s"] = round(time.time() - t, 1)
    _log(f"{len(blocks)} vineyard blocks, {sum(len(b['rows']) for b in blocks)} rows")
    if a.canopy == "unet":
        from .canopy_unet import apply_unet
        apply_unet(blocks, src, a.unet_weights, _log)

    # ---- waste
    t = time.time()
    from .waste import detect_waste
    waste = []
    if blocks:
        zone = unary_union([b["geometry"] for b in blocks]).buffer(20)
        excl = [g for g in (forbidden, passages.buffer(1.0) if passages is not None else None) if g is not None]
        waste = detect_waste(src, zone, [r["geometry"] for b in blocks for r in b["rows"]], _log,
                             exclude=unary_union(excl) if excl else None)
        for i, w in enumerate(sorted(waste, key=lambda w: (-w["geometry"].centroid.y, w["geometry"].centroid.x)), 1):
            d = sorted((b["geometry"].distance(w["geometry"]), b["vineyard_id"]) for b in blocks)
            w["vineyard_id"] = d[0][1] if d[0][0] <= 10 else ""
            w["waste_id"] = f"W{i:04d}"
    timings["waste_s"] = round(time.time() - t, 1)

    # ---- tables, measurements, layers
    canopy, rows, interrow, inspect = build_tables(blocks)
    gaps = {r["row_id"]: r.pop("gap_lines") for r in rows}
    layers = {
        "vineyard": _gdf(canopy, crs_out), "row": _gdf(rows, crs_out), "interrow_area": _gdf(interrow, crs_out),
        "waste": _gdf([dict(waste_id=w["waste_id"], vineyard_id=w["vineyard_id"], kind=w["kind"],
                            geometry=w["geometry"]) for w in waste], crs_out),
        "inspection": _gdf(inspect, crs_out),
        "row_gaps": _gdf([dict(row_id=k, length_m=round(g.length, 2), geometry=g)
                          for k, v in gaps.items() for g in v if g.length >= 5.0], crs_out),
        "blocks": _gdf([dict(vineyard_id=b["vineyard_id"], n_rows=len(b["rows"]), row_spacing_m=round(b["period"], 2),
                             area_m2=round(b["geometry"].area, 1), geometry=b["geometry"]) for b in blocks], crs_out),
    }
    meas = measurements(blocks, canopy, rows, interrow) if blocks else None
    if meas is not None:
        meas.to_csv(os.path.join(a.out, "measurements.csv"), index=False)

    # ---- route
    route = None
    if not a.no_route and blocks:
        t = time.time()
        start = _parse_start(a.start, a.start_crs, crs)
        bu = unary_union([b["geometry"] for b in blocks])
        if start is None:                          # default: headland point nearest to the vineyard centre
            ring = bu.buffer(3).boundary
            start = ring.interpolate(ring.project(bu.centroid))
            _log("no --start given: using a headland point next to the vineyard")
        if passages is None:                       # no authorised passages supplied: open ground is walkable
            area = aoi_geom if aoi_geom is not None else box(L, B, R, T)
            passages = area.difference(bu.buffer(0.3))
            if forbidden is not None:
                passages = passages.difference(forbidden)
            _log("no passages file: all open ground outside the vineyard counts as walkable")
        if a.route == "optimal":
            from .route_opt import plan_route_optimal
            route = plan_route_optimal(layers, start, passages, forbidden, src, _log, solve_s=a.solve_seconds)
        elif a.route == "lawnmower":
            from .lawnmower import plan_lawnmower
            route = plan_lawnmower(layers, start, passages, forbidden, src, _log)
        else:
            from .route import plan_route
            route = plan_route(layers, start, passages, forbidden, src, _log)
        route.crs = crs_out
        layers["route"] = route
        layers["start"] = _gdf([dict(name="START", geometry=Point(*route.attrs["start"]))], crs_out)
        timings["route_s"] = round(time.time() - t, 1)
        if route.attrs.get("legs"):
            from .instructions import Context, build_instructions
            tg = {}
            for lay, idc in (("inspection", "inspection_id"), ("waste", "waste_id")):
                for r in layers[lay].to_dict("records") if len(layers[lay]) else []:
                    tg[r[idc]] = dict(kind=lay, props=r, id=r[idc])
            ins = build_instructions(route.attrs["legs"], tg, Context(layers["interrow_area"], passages))
            with open(os.path.join(a.out, "instructions.txt"), "w", encoding="utf-8") as fh:
                for leg in ins:
                    fh.write(f"Leg {leg['leg']}: {leg['frm']} -> {leg['to']} ({leg['length_m']} m, total {leg['cumulative_m']} m)\n")
                    for i, s_ in enumerate(leg["steps"], 1):
                        fh.write(f"   {i}. {s_['text']}\n")
                    fh.write(f"   -> {leg['arrive']}\n\n")
        _roles(a, layers, start, passages, forbidden, src, crs_out)

    # ---- write layers (+ WGS84 copies)
    os.makedirs(os.path.join(a.out, "wgs84"), exist_ok=True)
    for k, g in layers.items():
        g.to_file(os.path.join(a.out, f"{k}.geojson"), driver="GeoJSON")
        if crs_out is not None and len(g):
            g.to_crs("EPSG:4326").to_file(os.path.join(a.out, "wgs84", f"{k}.geojson"), driver="GeoJSON")

    # ---- optional CVAT export (Sireț3 tile grid) and web viewer
    if hasattr(src, "x0") or a.cvat:
        try:
            from .cvat import write_cvat
            write_cvat(src, layers, os.path.join(a.out, "annotations.xml"), row_gaps=gaps)
            _log("CVAT annotations written")
        except Exception as e:                      # pragma: no cover
            _log(f"CVAT export skipped: {e}")
    if a.web and crs_out is not None:
        from .webexport import build_generic
        build_generic(a.out, src)

    tot = meas.iloc[0].to_dict() if meas is not None else {}
    summary = dict(input=a.input, kind=src.kind, crs=str(crs), extent_ha=round((R - L) * (T - B) / 1e4, 2),
                   blocks=len(blocks), rows=len(rows), plants=len(canopy), waste=len(waste), inspection=len(inspect),
                   row_length_m=tot.get("row_length_m"), canopy_area_m2=tot.get("canopy_area_m2"),
                   interrow_area_m2=tot.get("interrow_area_m2"),
                   route=({k: v for k, v in route.iloc[0].to_dict().items() if k != "geometry" and
                           k not in ("visit_order", "skipped", "unreachable")} if route is not None else None),
                   timings=dict(timings, total_s=round(time.time() - t0, 1)))
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=2, default=str)
    _log(f"done in {time.time() - t0:.0f} s -> {a.out}")
    return summary


def route_only(a):
    """Plan the walking route from GeoJSON files only (no imagery needed)."""
    t0 = time.time()
    layers = {}
    for k in ("interrow_area", "row", "inspection", "waste", "blocks", "row_gaps"):
        p = os.path.join(a.layers, f"{k}.geojson")
        layers[k] = gpd.read_file(p) if os.path.exists(p) else gpd.GeoDataFrame({"geometry": []}, geometry="geometry")
    crs = next((g.crs for g in layers.values() if g.crs is not None and len(g)), None)
    if crs is None or crs.is_geographic:
        raise SystemExit("layers must be in a metric CRS (use the files written by 'python -m vine run', not wgs84/)")
    set_crs(crs)
    for k, g in layers.items():
        if g.crs is None:
            layers[k] = g.set_crs(crs, allow_override=True)
    passages = forbidden = None
    zone_files = list(a.zones or []) + [f for f in (a.passages, a.forbidden) if f]
    if zone_files:
        from .route import load_zones
        passages, forbidden = load_zones(zone_files)
    bu = unary_union(list(layers["blocks"].geometry)) if len(layers["blocks"]) else         unary_union(list(layers["interrow_area"].geometry))
    start = _parse_start(a.start, a.start_crs, crs)
    if start is None:
        ring = bu.buffer(3).boundary
        start = ring.interpolate(ring.project(bu.centroid))
    if passages is None:
        from .route import coverage
        area = coverage(None, layers, None, start)
        passages = area.difference(bu.buffer(0.3))
        if forbidden is not None:
            passages = passages.difference(forbidden)
        _log("no passages file: all open ground outside the vineyard counts as walkable")
    if a.route == "none":                        # only the role routes
        _roles(a, layers, start, passages, forbidden, None, crs)
        _log(f"role routes written to {a.out} in {time.time() - t0:.0f} s")
        return
    if a.route == "optimal":
        from .route_opt import plan_route_optimal
        route = plan_route_optimal(layers, start, passages, forbidden, None, _log, solve_s=a.solve_seconds)
    elif a.route == "lawnmower":
        from .lawnmower import plan_lawnmower
        route = plan_lawnmower(layers, start, passages, forbidden, None, _log)
    else:
        from .route import plan_route
        route = plan_route(layers, start, passages, forbidden, None, _log)
    os.makedirs(os.path.join(a.out, "wgs84"), exist_ok=True)
    route = route.set_crs(crs, allow_override=True)
    route.to_file(os.path.join(a.out, "route.geojson"), driver="GeoJSON")
    route.to_crs("EPSG:4326").to_file(os.path.join(a.out, "wgs84", "route.geojson"), driver="GeoJSON")
    if route.attrs.get("legs"):
        from .instructions import Context, build_instructions
        tg = {}
        for lay, idc in (("inspection", "inspection_id"), ("waste", "waste_id")):
            for r in layers[lay].to_dict("records") if len(layers[lay]) and idc in layers[lay] else []:
                tg[r[idc]] = dict(kind=lay, props=r, id=r[idc])
        ins = build_instructions(route.attrs["legs"], tg, Context(layers["interrow_area"], passages))
        with open(os.path.join(a.out, "instructions.txt"), "w", encoding="utf-8") as fh:
            for leg in ins:
                fh.write(f"Leg {leg['leg']}: {leg['frm']} -> {leg['to']} ({leg['length_m']} m, total {leg['cumulative_m']} m)\n")
                for i, s_ in enumerate(leg["steps"], 1):
                    fh.write(f"   {i}. {s_['text']}\n")
                fh.write(f"   -> {leg['arrive']}\n\n")
    _roles(a, layers, start, passages, forbidden, None, crs)
    _log(f"route written to {a.out} in {time.time() - t0:.0f} s")


def _roles(a, layers, start, passages, forbidden, src, crs):
    roles = [r for r in (a.roles or "").split(",") if r in ("farmer", "inspector")]
    if not roles:
        return
    from .roles import write_roles
    t = time.time()
    res = write_roles(a.out, layers, start, passages, forbidden, src, _log, roles=roles, farmer_lanes=a.farmer_lanes,
                      turn_radius=a.turn_radius, farmer_speed=a.farmer_speed, max_minutes=a.max_minutes,
                      solve_s=a.solve_seconds, crs=crs)
    for k, g in res.items():
        layers["route_" + k] = g
    _log(f"role routes ({', '.join(roles)}) in {time.time() - t:.0f} s")


def _role_args(p):
    p.add_argument("--roles", default="farmer,inspector",
                   help="extra role routes: farmer,inspector (comma list; 'none' to skip)")
    p.add_argument("--farmer-lanes", choices=["all", "cover"], default="all",
                   help="farmer works every inter-row (all) or just enough to reach every row (cover)")
    p.add_argument("--turn-radius", type=float, default=0.0, help="farmer vehicle turning radius in m (0 = on foot)")
    p.add_argument("--farmer-speed", type=float, default=4.0, help="farmer working speed km/h")
    p.add_argument("--max-minutes", type=float, default=None, help="inspector time budget (minutes, 4 km/h)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m vine", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="detect vineyard objects, measure, plan the walking route")
    r.add_argument("input", help="GeoTIFF file, folder of GeoTIFF tiles, or JPG/PNG (with --gsd)")
    r.add_argument("--out", default="result")
    r.add_argument("--gsd", type=float, default=None, help="ground resolution in m/px for images without georeference")
    r.add_argument("--aoi", default=None, help="GeoJSON polygon: only analyse this area")
    r.add_argument("--start", default=None, help="start point: GeoJSON file or 'x,y' (in --start-crs)")
    r.add_argument("--start-crs", default=None, help="CRS of an 'x,y' start, e.g. EPSG:4326 for 'lon,lat'")
    r.add_argument("--zones", nargs="*", default=None, help="GeoJSON polygons with property type=passage|forbidden")
    r.add_argument("--passages", default=None, help="GeoJSON of authorised passages (type=passage)")
    r.add_argument("--forbidden", default=None, help="GeoJSON of forbidden zones (type=forbidden)")
    r.add_argument("--canopy", choices=["cive", "unet", "rule"], default="cive")
    r.add_argument("--unet-weights", default="model/canopy_unet.pth")
    r.add_argument("--route", choices=["optimal", "tsp", "lawnmower"], default="optimal")
    r.add_argument("--solve-seconds", type=float, default=45)
    r.add_argument("--no-route", action="store_true")
    r.add_argument("--row-spacing", default=None, help="expected row spacing range in m, e.g. 1.5,3.8 (default 2.0,3.8)")
    r.add_argument("--model", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  "model", "vine_model_v1.json"))
    r.add_argument("--cvat", action="store_true", help="also write CVAT annotations.xml")
    r.add_argument("--web", action="store_true", help="also build a static web viewer in OUT/web")
    q = sub.add_parser("route", help="plan the walking route from GeoJSON layers only (no imagery)")
    q.add_argument("layers", help="folder with interrow_area/row/inspection/waste/blocks.geojson (metric CRS)")
    q.add_argument("--out", default="route_result")
    q.add_argument("--start", default=None, help="start point: GeoJSON file or 'x,y' (in --start-crs)")
    q.add_argument("--start-crs", default=None)
    q.add_argument("--zones", nargs="*", default=None)
    q.add_argument("--passages", default=None)
    q.add_argument("--forbidden", default=None)
    q.add_argument("--route", choices=["optimal", "tsp", "lawnmower", "none"], default="optimal",
                   help="challenge route planner ('none' = only the --roles routes)")
    q.add_argument("--solve-seconds", type=float, default=45)
    _role_args(r)
    _role_args(q)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a)
    elif a.cmd == "route":
        route_only(a)


if __name__ == "__main__":
    main(sys.argv[1:])
