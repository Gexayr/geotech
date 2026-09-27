"""End-to-end tests on synthetic vineyards: any row direction, any projection, route legality.

    python -m pytest tests -q
"""
import json
import os
import sys

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from synth import make_vineyard                      # noqa: E402
from vine.cli import main                            # noqa: E402


def run_cli(tmp_path, truth, *extra):
    out = str(tmp_path / "out")
    x, y = truth["start"]
    main(["run", truth["path"], "--out", out, "--solve-seconds", "5", *extra] +
         (["--start", f"{x},{y}", "--start-crs", truth["crs"]] if "--no-route" not in extra else []))
    return out, json.load(open(os.path.join(out, "summary.json")))


@pytest.mark.parametrize("angle", [0, 15, 60, 105, 150])
def test_rows_any_direction(tmp_path, angle):
    t = make_vineyard(str(tmp_path / f"v{angle}.tif"), angle_deg=angle, seed=angle)
    out, s = run_cli(tmp_path, t, "--no-route")
    assert s["blocks"] == 1
    assert abs(s["rows"] - t["n_rows"]) <= 1, s
    assert 0.6 * t["canopy_m2"] < s["canopy_area_m2"] < 1.4 * t["canopy_m2"], s
    blocks = gpd.read_file(os.path.join(out, "blocks.geojson"))
    assert abs(blocks["row_spacing_m"].iloc[0] - t["spacing"]) < 0.25


def test_gap_found_and_route_legal(tmp_path):
    t = make_vineyard(str(tmp_path / "v.tif"), angle_deg=35, seed=3)
    out, s = run_cli(tmp_path, t)
    insp = gpd.read_file(os.path.join(out, "inspection.geojson"))
    gx, gy = t["gaps"][0]
    assert insp.distance(Point(gx, gy)).min() < 3.0          # the planted 7 m gap is an inspection target
    r = gpd.read_file(os.path.join(out, "route.geojson")).iloc[0]
    line = r.geometry
    sx, sy = t["start"]
    assert Point(line.coords[0]).distance(Point(sx, sy)) < 5 and Point(line.coords[-1]).distance(Point(sx, sy)) < 5
    assert r["outside_passable_pct"] <= 2.0
    assert r["n_targets"] >= 1
    assert os.path.exists(os.path.join(out, "instructions.txt"))


def test_latlon_input_is_warped_to_utm(tmp_path):
    t = make_vineyard(str(tmp_path / "ll.tif"), angle_deg=70, seed=7, latlon=True)
    out, s = run_cli(tmp_path, t, "--no-route")
    assert "32633" in s["crs"]                               # geographic input -> local UTM zone
    assert abs(s["rows"] - t["n_rows"]) <= 1
    assert os.path.exists(os.path.join(out, "wgs84", "row.geojson"))


def test_narrow_rows_with_row_spacing_option(tmp_path):
    t = make_vineyard(str(tmp_path / "n.tif"), angle_deg=20, spacing=1.8, plant_spacing=1.0, seed=11)
    out, s = run_cli(tmp_path, t, "--no-route", "--row-spacing", "1.4,3.0")
    assert abs(s["rows"] - t["n_rows"]) <= 1, s


def test_no_vineyard_gives_empty_result(tmp_path):
    t = make_vineyard(str(tmp_path / "e.tif"), block=(0.5, 0.5), gaps=(), seed=5)
    out, s = run_cli(tmp_path, t, "--no-route")
    assert s["blocks"] == 0 and s["plants"] == 0


def test_route_from_geojson_only(tmp_path):
    """Another computer with only the GeoJSON layers (no imagery) can re-plan the route."""
    import shutil
    t = make_vineyard(str(tmp_path / "g.tif"), angle_deg=45, seed=9)
    out, _ = run_cli(tmp_path, t, "--no-route")
    geo = tmp_path / "layers_only"
    geo.mkdir()
    for f in os.listdir(out):
        if f.endswith(".geojson"):
            shutil.copy(os.path.join(out, f), geo / f)
    os.remove(t["path"])                                      # the image is gone
    x, y = t["start"]
    main(["route", str(geo), "--out", str(tmp_path / "r"), "--start", f"{x},{y}", "--solve-seconds", "3"])
    r = gpd.read_file(str(tmp_path / "r" / "route.geojson")).iloc[0]
    assert Point(r.geometry.coords[0]).distance(Point(x, y)) < 5 and r["outside_passable_pct"] <= 2.0


def test_farmer_and_inspector_routes(tmp_path):
    """Farmer works every inter-row exactly once; inspector visits the gap; tractor mode uses skip turns."""
    import json
    t = make_vineyard(str(tmp_path / "f.tif"), angle_deg=20, seed=4)
    out, _ = run_cli(tmp_path, t, "--no-route")
    x, y = t["start"]
    main(["route", out, "--out", str(tmp_path / "foot"), "--route", "none", "--start", f"{x},{y}", "--solve-seconds", "3"])
    main(["route", out, "--out", str(tmp_path / "trac"), "--route", "none", "--roles", "farmer", "--turn-radius", "4",
          "--start", f"{x},{y}", "--solve-seconds", "3"])
    n_ir = len(gpd.read_file(os.path.join(out, "interrow_area.geojson")))
    f = gpd.read_file(str(tmp_path / "foot" / "route_farmer.geojson")).iloc[0]
    assert f["lanes_worked"] == f["lanes_total"] == n_ir
    assert len(set(f["lane_order"].replace("r", "").split(","))) == n_ir          # each lane once
    assert Point(f.geometry.coords[0]).distance(Point(x, y)) < 1                  # closed tour at the start
    assert Point(f.geometry.coords[-1]).distance(Point(x, y)) < 1
    assert f["efficiency_pct"] > 60
    tr = gpd.read_file(str(tmp_path / "trac" / "route_farmer.geojson")).iloc[0]
    assert tr["skip_turns"] > 0
    i = gpd.read_file(str(tmp_path / "foot" / "route_inspector.geojson")).iloc[0]
    assert i["n_targets"] >= 1 and i["outside_passable_pct"] <= 2.0
    rj = json.load(open(tmp_path / "foot" / "roles.json"))
    assert rj["farmer"]["steps"] and rj["inspector"]["checklist"]
    g = gpd.read_file(str(tmp_path / "foot" / "graph_farmer.geojson"))
    assert (g["kind"] == "work").sum() == n_ir
