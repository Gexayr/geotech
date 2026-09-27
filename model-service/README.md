# Vineyard AI: production package

Everything we run on localhost, in one folder: the detection algorithms, the route planners
(graph path finders), the web app and its pages, model parameters and a sample map.
It works on **any** vineyard orthomosaic (GeoTIFF in any projection, a folder of tiles, or JPG/PNG with a ground resolution).
It needs no GPU and no training.

**For geotech-app integration** see **[MODEL_SERVICE.md](MODEL_SERVICE.md)**: the `geotech-model` service (port 9090) implements the agent-brief contract (`/health`, `/v1/detect`, `/v1/measurements`, `/v1/route` with farmer / auditor). Build it with `Dockerfile.model` + `docker-compose.model.yml`.

## 1. Start on localhost

| how | command | then open |
|---|---|---|
| Windows, one click | `run_local.bat` | http://localhost:8000 |
| Linux / macOS | `./run_local.sh` | http://localhost:8000 |
| Docker | `docker compose up --build` | http://localhost:8000 |
| manual | `python -m venv .venv` → `pip install -r requirements.txt` → `python app/server.py` | http://localhost:8000 |

Python 3.10 is required. The first start installs the dependencies (about 2 minutes).

## 2. Pages

| URL | page |
|---|---|
| `/` | Sireț3 result map: canopies, rows, inter-rows, waste, inspection points, challenge route |
| `/roles` | **Farmer and inspector routes** on Sireț3: two graphs, a role switch, instructions and a checklist |
| `/test` | Upload **any** map, draw the area, click the start, choose roles, then run. Shows the layers, both routes, instructions and a ZIP download |
| `/bench` | Benchmark of the segmentation methods |
| `/healthz` | Health check (JSON) |

## 3. The two route algorithms (two different graphs)

| | **Farmer** (field work) | **Inspector** (scouting) |
|---|---|---|
| goal | work **every inter-row once** (spraying, mowing, pruning, harvest) | visit only the **problem spots**: row gaps and waste |
| graph nodes | both ends of every inter-row lane, plus the start | the start plus every target stop |
| graph edges | *work* (along a lane), *headland* (turn between lanes on the same side), *transfer* (shortest walkable path between blocks) | shortest **legal** paths between stops (authorised passages and inter-rows; ≤ 1.9 % off them) |
| problem | Rural Postman, solved as a generalised TSP over oriented lanes (each lane appears A→B and B→A, and exactly one is used) | prize-collecting TSP / orienteering; priorities are waste 3 and gaps 1 + gap length / 10 m; optional time budget |
| solver | nearest-neighbour start, then OR-Tools guided local search | OR-Tools guided local search with a hard off-path budget |
| vehicle | `turn_radius` (0 = on foot). With a tractor radius the solver picks **skip-row** turns by itself, because sharp turns cost a fishtail manoeuvre | on foot, 4 km/h |
| outputs | `route_farmer.geojson`, `graph_farmer.geojson`, `instructions_farmer.txt` | `route_inspector.geojson`, `graph_inspector.geojson`, `instructions_inspector.txt`, `inspector_checklist.csv` |

Both are closed tours (they start and finish at the start point). Both are also written to `roles.json` in lon/lat, which is the easiest payload for a frontend or a mobile app.

The **challenge route** (`route.geojson`, `--route optimal`) is the competition version of the inspector route. It maximises the number of targets under the 2 % off-passable rule.

## 4. Command line (batch, any map)

```bash
# full pipeline on a new map: detection + measurements + challenge route + farmer + inspector
python -m vine run maps/farm.tif --out results/farm --start 28.7074,47.1230 --start-crs EPSG:4326 --web

# tractor with a 4 m turning radius, inspector limited to 60 minutes
python -m vine run maps/farm.tif --out results/farm --turn-radius 4 --max-minutes 60

# re-plan routes from existing result layers only (no image needed), e.g. with a new start point
python -m vine route results/farm --out results/farm_new --route none --roles farmer,inspector --start 28.7080,47.1235 --start-crs EPSG:4326

# try it on the included sample
python -m vine run examples/siret3_sample.tif --out results/sample --web
```

| option | meaning |
|---|---|
| `--start lon,lat --start-crs EPSG:4326` or `--start start.geojson` | start / finish point (default: headland next to the vineyard) |
| `--zones passages.geojson forbidden.geojson` | authorised passages and forbidden zones (property `type` = `passage` / `forbidden`) |
| `--roles farmer,inspector` | which role routes to plan (`none` = skip) |
| `--turn-radius 4` | farmer vehicle turning radius in m (0 = on foot) |
| `--farmer-lanes all\|cover` | every inter-row, or only enough inter-rows that every row is reached once |
| `--farmer-speed 6` | farmer speed in km/h, used for the time estimate |
| `--max-minutes 60` | inspector time budget; the most important spots are kept first |
| `--route optimal\|tsp\|lawnmower\|none` | challenge route planner |
| `--aoi area.geojson` | analyse only this polygon |
| `--row-spacing 1.4,3.0` | expected row spacing in m (for dense vineyards) |
| `--gsd 0.03` | m/px, for JPG/PNG without georeference |

Outputs are written to `--out`:
- **Metric CRS layers:** `vineyard`, `row`, `interrow_area`, `waste`, `inspection`, `blocks`, `row_gaps`, `route*`, `graph_*` (`.geojson`).
- **Web-map copies:** the same layers under `wgs84/`.
- **Tables and payloads:** `measurements.csv`, `summary.json`, `roles.json`, `instructions*.txt`, `inspector_checklist.csv`.
- **Static viewer (with `--web`):** `web/`.

## 5. HTTP API (for the frontend)

| method | endpoint | body / answer |
|---|---|---|
| POST | `/api/upload` | multipart `file` (+ `gsd` for JPG/PNG) → `{id, kind, width, height, gsd_m, preview_w, preview_h, crs, size_m}` |
| POST | `/api/sample` | loads `examples/siret3_sample.tif` → same answer as upload |
| GET | `/api/image/<id>/preview.jpg` | preview image (pixel space of all returned geometry) |
| POST | `/api/run` | `{id, aoi:[[x,y],...], start:[x,y], options:{roles:["farmer","inspector"], turn_radius:0, farmer_lanes:"all", max_minutes:null}}` in preview pixels → `{job}` |
| GET | `/api/job/<job>?since=N` | `{status: running\|done\|error, log:[...], n, result}` |
| GET | `/api/job/<job>/files` | ZIP with all GeoJSON layers (world coordinates), CSVs and instructions |

`result` contains:
- `layers.{blocks, vineyard, row, interrow_area, waste, inspection}`: GeoJSON in preview pixels, with y negated for Leaflet `CRS.Simple`.
- `roles.farmer = {route, graph, summary, steps[{kind, text, length_m, coords}]}`.
- `roles.inspector = {route, graph, summary, instructions[legs], checklist[...]}`.
- `summary`: counts, lengths and areas.

Environment variables:
- `VINE_MAX_JOBS`: concurrent runs (default 1; a run takes 1 to 3 min per hectare on 1 CPU).
- `CANOPY=unet`: use the trained U-Net. This needs `requirements-ml.txt`.
- `HOST` and `PORT`.

## 6. Folder map

```
app/server.py        web server + API (waitress)
vine/                algorithms
  candidates.py      vineyard detection (FFT row periodicity)
  rows.py            rows, gaps, per-plant canopy (CIVE + Otsu), inter-rows
  assemble.py        blocks, IDs, measurements
  waste.py           waste detection
  route.py           walkability graph (0.5 m grid) + TSP route
  route_opt.py       challenge / inspector solver (prize-collecting TSP, off-path budget)
  roles.py           FARMER + INSPECTOR planners, graphs, roles.json
  lawnmower.py       boustrophedon sweep (alternative)
  instructions.py    turn-by-turn text
  source.py          readers for any GeoTIFF / tile folder / JPG
  cli.py             python -m vine run | route
web/                 index.html, roles.html, test.html, bench.html + data/ (Sireț3 results)
model/               vine_model_v1.json (parameters), canopy_unet.pth (optional U-Net)
examples/            siret3_sample.tif + Sireț3 passages / forbidden / start
tests/               pytest on synthetic vineyards (any angle, lat/lon, gaps, empty field)
```

## 7. Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```
