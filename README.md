# Vineyard AI Field Challenge — Marcaj

DeepTech GigaHack 2026, 25–27 Sept, Chișinău. Deadline: **Sun 27 Sept, 15:00
(Chișinău time)**. Official rules/annotation/scoring PDFs: `03_docs/` (from
the organizer assets archive).

## Current state — real data, no synthetic geometry

Everything below runs on the **actual challenge assets** (`02_route/`,
`05_examples/` — see repo root), not placeholder geometry:

- **Route infrastructure** — the organizer-supplied `start.geojson`,
  `passages.geojson`, `forbidden.geojson`, `study_area.geojson`
  (EPSG:32635), served as-is.
- **Example annotated tiles** — the 2 reference tiles from
  `05_examples/siret3_examples_cvat.zip`, parsed from their real **CVAT for
  images 1.1** export (`backend/app/services/cvat_import.py`), with pixel
  coordinates converted to world EPSG:32635 via each tile's *actual*
  GeoTIFF geotransform (`backend/app/services/georef.py`) — not guessed.
  The real orthophoto is served as the map background (GeoTIFF → JPEG).
- **Measurements** — canopy/inter-row area (polygon union) and row length,
  aggregated by `vineyard_id`/`row_id` **across every loaded tile**
  (`backend/app/services/measurements.py`), matching the brief's rule that a
  block/row split across tiles keeps one ID. Produces `measurements.csv` at
  the repo root, byte-for-byte the submission artifact — regenerate any time
  with `curl localhost:8000/api/site/measurements.csv -o measurements.csv`.
- **Walking route** — real, not a demo. Targets are *derived*, not
  annotated (per the brief: inspection locations are our own app's output):
  a `row_structure=disrupted` row → a gap target at its midpoint; a `waste`
  box → a target at its centroid (`backend/app/services/targets.py`).
  Pathfinding rasterizes the real passages ∪ inter-row areas minus
  forbidden ∪ canopies and runs A* (`backend/app/services/pathfinding.py`),
  then solves the closed tour (exact permutation ≤8 targets, else
  nearest-neighbour) in `backend/app/services/real_route.py`. On the current
  2-tile example set this already finds 5 real disrupted-row gaps in block
  V02 and produces a genuine ~1963 m tour vs. ~9518 m visiting them
  separately (79% shorter) — see `route.geojson` at the repo root, real
  EPSG:32635 coordinates, regenerate with
  `curl localhost:8000/api/site/route.geojson -o route.geojson`.
- **Frontend** — React + Vite + react-leaflet, `L.CRS.Simple` over a local
  metre plane that is real EPSG:32635 minus the study area's SW corner (a
  pure translation, so all distances/areas are unaffected) — tiles, route
  infra and the derived route all line up on one map.

The old synthetic `V001` demo (`backend/app/data/demo_v001.py`,
`/api/blocks/*`) is still in the repo for reference but no longer used by
the frontend.

## Run it

```bash
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:5173

Or without Docker:

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

## Roadmap (the brief's 6 stages)

| # | Stage | Status |
|---|-------|--------|
| 1 | Prepare imagery (tile GeoTIFF, keep coords + tile IDs) | done by the organizers — 311 real tiles in `01_tiles/`, unused ones just need extracting |
| 2 | AI pre-annotation (canopies/rows/waste) | **owned by a teammate** — model/Marcaj upload in progress |
| 3 | Review in Marcaj | real schema confirmed: **CVAT for images 1.1**, labels `vineyard`/`waste`/`row`/`interrow_area` exactly as in `03_docs/Vineyard_AI_annotation_rules.pdf` — `cvat_import.py` already parses it |
| 4 | Measure | **done, real** — `measurements.py`, aggregates across all loaded tiles by vineyard_id/row_id |
| 5 | Plan a route | **done, real** — `real_route.py` + `pathfinding.py`, real passages/forbidden/inter-row geometry; 0 targets until the full annotation export lands more disrupted rows/waste (already found 5 on the example set) |
| 6 | Build the web app | done — real tiles/photo/annotations/route on one map |

## Classical-CV fallback (no trained model, no GPU)

`backend/scripts/classical_pre_annotate.py` — a stopgap pre-annotator in
case the team's real model isn't ready in time. No neural network: Otsu
threshold on the excess-green index for a vegetation mask, Hough transform
for the dominant row angle, a smoothed projection profile for row
positions, fixed-1.2m-interval splitting along each row for canopy pieces
(the annotation rules' own fallback for "no visible narrowing"), and
geometric interrow polygons between adjacent rows. **No waste detection at
all** — unreliable without training data, and a false box costs as much as
a missed one.

Calibrated against the 2 real ground-truth tiles before running on all 311
(see the script's docstring for the full numbers): canopy area within
~2–20% of ground truth, inter-row area IoU 0.81–0.89, row recovery 24/25
and 22/26 with a closely matching spacing pattern. Individual canopy
*instances* are approximate (fixed-interval splitting, not real plant
boundaries) — don't expect this to compete with a trained model on the
canopy-instance F1 metric, just to provide real coverage everywhere a
trained model hasn't landed yet.

`app.services.cvat_import.load_merged()` combines both sources — the 2
real ground-truth tiles take priority, the classical-CV output (230 of the
remaining 309 tiles look like vineyard rows; 81 don't and are correctly
left empty) fills the rest. This is what the backend now serves by
default. Re-run the script any time the tile set changes; swap in the
team's real model export by pointing `FALLBACK_PATH` (or a new source) at
it in `cvat_import.py`.

**Route scale note:** across the full merged dataset there are 744 derived
targets (disrupted-row gaps + waste) — too many for one literal tour of
the whole 145ha site (`MAX_TARGETS_FOR_ROUTE = 40` in `real_route.py`
returns a clear message rather than hanging). Use `/route/custom` (or the
frontend's route planner) with a `tiles=`/`area=` scope to route one block
or session at a time — which is also just how this would work in practice
(nobody inspects the entire farm in one walk).

### Known gaps / next steps
- Only the 2 example tiles are wired in; once the teammate's full Marcaj
  export (up to 311 tiles) is available, point `cvat_import.load_examples()`
  (or a new loader) at it — every downstream service (measurements, targets,
  route) already operates on "however many tiles are loaded," no changes
  needed.
- Pathfinding is a 2 m-resolution grid + pure-Python A*; fine for a handful
  of targets, revisit (finer grid / vectorized shortest-path) if the full
  export yields dozens of disrupted rows.
- TSP is exact permutation search up to 8 targets, nearest-neighbour above
  that — add 2-opt improvement if target counts grow large and tour quality
  starts to matter.
- Model weights + reproduction instructions + processing time/hardware
  still need to go in this README before submission (owned by the teammate
  running the model).
