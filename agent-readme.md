# Agent brief: detection model, measurements and routes

This file is for the agent (or team) building the **AI model** for the
Vineyard AI Field Challenge app (`geotech.amsoft.am`). It covers what already
exists, what we need from you, and the exact contract between our backend and
your service.

**Short version.** You own the "brain": detection, counting and measurements,
and the walking routes for both the **farmer** and the **auditor**. Our side
(this repo) owns the web app, the map, uploads, storage and exports. After
integration our backend **computes nothing itself**: it asks your service and
displays the answer. Our current classical-CV and route code stays only as a
fallback for when your service is down.

---

## 1. Data you will work with

| What | Details |
|---|---|
| Tiles | GeoTIFF, **2048 × 2048 px, 3 bands RGB uint8**, **EPSG:32635**, **0.025 m/px** (one tile ≈ 51.2 × 51.2 m). Name pattern `siret3_rRRR_cCCC.tif` (grid row/column). The full organizer set is **311 tiles** (`01_tiles/` in the organizer archive). The server currently holds only the tiles operators uploaded. |
| Ground truth | 2 human-annotated tiles, `siret3_r021_c012.tif` and `siret3_r006_c004.tif`, in `backend/app/data/real/examples/annotations.xml` (CVAT for images 1.1). Totals: 650 canopies, 51 rows (5 `disrupted`), 49 inter-row areas, 0 waste boxes. |
| Route infrastructure | `backend/app/data/real/route/`: `start.geojson` (start point), `passages.geojson` (authorised walking passages), `forbidden.geojson` (no-go zones), `study_area.geojson`. All in EPSG:32635. |
| Annotation rules | `03_docs/Vineyard_AI_annotation_rules.pdf` from the organizer archive. This is the source of truth for labels and attributes. |

### Label schema (same as Marcaj / CVAT, must be kept)

| Label | Geometry | Attributes |
|---|---|---|
| `vineyard` | polygon, one per canopy / plant instance | `vineyard_id` |
| `row` | polyline along the row axis | `vineyard_id`, `row_id`, `row_structure` ∈ `regular` / `disrupted` / `unassessable` |
| `interrow_area` | polygon between two rows | `vineyard_id`, `interrow_cover` ∈ `bare_soil` / `vegetation` / `mixed` / `unassessable` |
| `waste` | box | `vineyard_id` |

**ID rule.** A block or row that crosses a tile edge must keep **the same
`vineyard_id` / `row_id` on every tile**. Measurements are merged by these IDs
across tiles. Our classical CV cannot do this: it uses `AUTO-<tile>` per tile,
so every tile looks like its own block. Your service needs to handle it.

---

## 2. What already exists (besides our classical CV)

### Web app (React + Leaflet), two roles: Farmer and Auditor
- Map of the whole site: tile photos, study area, passages and forbidden
  zones. Detection layers (canopies, rows, inter-rows, waste, targets, route)
  are drawn for every annotated tile in view. The map zooms out to about a
  2 km scale, and a button centres it on the selected tile at a 100 m scale.
- Tile selector, tile upload (up to 10 GeoTIFFs per request, 50 MB per file,
  100 MB per request) and tile delete with a confirmation dialog.
- Per-tile metrics panel, a Rows table (row_id and length) and a Targets list.
- **Route planner, available to both roles:** custom start point (click on
  the map), scope by tiles or by a hand-drawn area, and "reset to default".
  The route summary shows the length and the saving compared with separate
  trips.
- Hand-drawn "vineyard area" polygons (both roles), stored on the server.
- Exports: annotation JSON per tile, route infrastructure JSON,
  `measurements.csv`, `route.geojson` (EPSG:32635), and a CVAT XML for each
  upload request.
- Loaders and spinners for slow actions. Mobile layout with a bottom sheet.

### Backend (FastAPI, `backend/app`)
| Module | What it does today (this becomes **your** job, ours stays as fallback) |
|---|---|
| `services/georef.py` | Pixel → EPSG:32635 via each GeoTIFF's affine transform. **Stays ours.** |
| `services/cvat_import.py` / `cvat_export.py` | CVAT XML ↔ internal per-tile format. **Stays ours.** |
| `services/classical_cv.py` | Detection without ML: ExG + Otsu, Hough row angle, projection peaks, fixed 1.2 m canopy split, **no waste**. To be replaced by your model. |
| `services/measurements.py`, `real_metrics.py` | Canopy and inter-row area (polygon union), row length (summed over pieces with the same `row_id`), counts, aggregated by `vineyard_id`. To be replaced by yours. |
| `services/targets.py` | Route targets: a `disrupted` row gives a target at its midpoint, a waste box gives a target at its centroid. To be replaced by yours. |
| `services/pathfinding.py`, `real_route.py` | Walkable grid at 2 m = passages ∪ inter-rows − forbidden − canopies. A* between points, then a closed tour (exact search up to 8 targets, nearest-neighbour above, maximum 40 targets). To be replaced by yours. |
| `api/site.py` | All HTTP endpoints the frontend uses (see §5). **Stays ours.** It will call your service. |

Internally the app uses a **local metre plane** = EPSG:32635 minus the study
area's south-west corner (a pure translation). You never need to deal with
it: **you talk in pixel coordinates (per tile) or in EPSG:32635**, and we
convert.

---

## 3. What we need from you

### 3.1 Detection (per tile)
For each tile: canopy polygons, row polylines with `row_structure`, inter-row
polygons with `interrow_cover`, and waste boxes. Include a `confidence` on
every object so the auditor view can flag weak detections. Tiles that are not
vineyard (village, roads, forest) must come back **empty**. False objects cost
as much as missed ones in scoring.

### 3.2 Counting and measurements (whole site or a scope)
- Per row: `row_id`, length in metres.
- Per block (`vineyard_id`): canopy area, inter-row area, canopy count, row
  count, total row length.
- Site total. Blocks and rows split across tiles are merged by ID. Areas are
  polygon **unions**, not sums, so overlaps across tile edges do not double
  count.

### 3.3 Routes: two different ones, both produced by you
- **Farmer route.** A walking tour for field work. Targets are row gaps
  (`disrupted` rows or missing plants) and waste to collect. The route starts
  and ends at the start point (default: `start.geojson`, or one the user
  picked). It must follow **authorised passages and inter-row areas only**:
  never through canopies or forbidden zones.
- **Auditor route.** A tour for **verifying** the results, for example a
  sample of rows and blocks to check counts, lengths and areas, weighted
  towards low-confidence detections. **You define the target logic** and
  document it. The walking constraints are the same as for the farmer.
- Both routes accept a scope: a list of tiles and/or a polygon area, plus an
  optional custom start.
- Return the ordered targets, the route polyline, its length, and a
  comparison with "separate trips" (the UI already shows this).

### 3.4 Offline batch mode
A command that runs detection on a folder of tiles and writes **one CVAT for
images 1.1 XML** with the schema above. It is needed for review in Marcaj and
for the submission.

### 3.5 Deliverables
- A `Dockerfile` for your service, with weights included or downloaded at
  build time.
- A README: how to reproduce training and inference, **processing time per
  tile and the hardware used** (a submission requirement), and accuracy on
  the 2 ground-truth tiles (canopy IoU and instance F1, row recovery, area
  error).

### 3.6 Hard constraints: our server
- **CPU only: 4 cores, no GPU. 3.8 GB RAM in total**, shared with other
  services. Budget: **≤ 1.5 GB RAM** for your container.
- **Latency.** Detection **≤ 25 s per tile**: an upload of 10 tiles must
  finish within the 300 s proxy timeout. A route **≤ 60 s** for a scope of up
  to about 40 targets.
- Use ONNX / quantised weights, or tile-level downscaling, if needed to fit.

---

## 4. Integration architecture (decided)

```
 browser ──► nginx ──► geotech-app (our FastAPI, :9080) ──HTTP──► geotech-model (yours, :9090)
                            │                                        │
                            └──── shared host folders (read-only for you) ─┘
                                  backend/app/data/real/tiles  → /data/tiles
                                  backend/app/data/real/route  → /data/route
                                  (your own writable cache)    → /data/cache
```

- Your service runs as a **separate Docker container** named `geotech-model`
  on the same host. It is on a shared Docker network with `geotech-app` and is
  **not exposed to the internet**. We call `http://geotech-model:9090`. The
  URL comes from our env var `MODEL_URL`.
- **No image bytes over HTTP.** You read GeoTIFFs directly from `/data/tiles/<tile_name>`,
  and we send only tile names.
- **Your service is stateful for detections.** Cache per-tile detections in
  `/data/cache`, so measurements and routes do not re-run the model. We tell
  you when a tile is added (detect) or deleted (DELETE).
- **Stack is your choice** (FastAPI recommended). It must speak the JSON
  contract below.
- We (this repo) will write the client (`services/model_client.py`), the
  fallback switch, and the docker/deploy wiring.

---

## 5. HTTP contract

Conventions:
- JSON everywhere, UTF-8.
- **Pixel coordinates**: `[x, y]`, origin at the top-left of the 2048×2048
  tile, x to the right, y down (same as CVAT).
- **World coordinates**: `[x, y]` in EPSG:32635 metres.
- Errors: a non-2xx status with `{"detail": "..."}`. Use `404` for an unknown
  tile (not in `/data/tiles`), `422` for bad input, and `503` while the model
  is loading.

### `GET /health`
```json
{ "status": "ok", "model_version": "vineyard-seg-1.2.0", "ready": true }
```
We call this on startup and before each batch. If it fails or times out
(2 s), we fall back to our classical CV.

### `POST /v1/detect`: run the model on tiles (called on upload)
Request:
```json
{ "tiles": ["siret3_r034_c025.tif", "siret3_r035_c025.tif"], "force": false }
```
`force: true` re-runs even if the tile is cached (for example after a
re-upload with the same name).

Response (pixel coordinates, one entry per requested tile):
```json
{
  "model_version": "vineyard-seg-1.2.0",
  "tiles": {
    "siret3_r034_c025.tif": {
      "canopies":  [ { "points": [[x,y],...], "vineyard_id": "V03", "confidence": 0.91 } ],
      "rows":      [ { "points": [[x,y],...], "vineyard_id": "V03", "row_id": "V03-R07",
                       "row_structure": "disrupted", "confidence": 0.84 } ],
      "interrows": [ { "points": [[x,y],...], "vineyard_id": "V03",
                       "interrow_cover": "bare_soil", "confidence": 0.88 } ],
      "waste":     [ { "bbox": [xtl, ytl, xbr, ybr], "vineyard_id": "V03", "confidence": 0.77 } ]
    }
  },
  "errors": { "some_bad_tile.tif": "not a vineyard tile / unreadable / ..." },
  "elapsed_s": 14.2
}
```
- `points` for polygons do not need to be closed. We close them.
- `vineyard_id` and `row_id` **must be consistent across tiles** (see §1).
  If a later tile reveals that two IDs are the same block, return the merged
  IDs from `/v1/measurements` and `/v1/route`. We re-read detections on every
  request, so IDs may change over time.
- This shape is a superset of what our `classical_cv.process_tile()` returns,
  so it drops straight into the existing pipeline.

### `DELETE /v1/tiles/{tile_name}`: forget a tile
We call this when a user deletes a tile. Drop it from your cache, measurements
and routes. `200 {"deleted": "<name>"}`, or `404` if unknown.

### `POST /v1/measurements`: counts and measurements
Request (omit `tiles` to use every detected tile, `area_world` optional):
```json
{ "tiles": ["siret3_r034_c025.tif"], "area_world": [[x,y], ...] }
```
Response. Keep **exactly these field names**, since the UI and
`measurements.csv` are built on them:
```json
{
  "block_count": 3, "row_count": 41,
  "canopy_area_m2": 812.4, "canopy_area_ha": 0.08124,
  "interrow_area_m2": 5120.9, "interrow_area_ha": 0.51209,
  "total_row_length_m": 1893.2, "waste_count": 2, "tiles_loaded": 12,
  "blocks": [
    {
      "vineyard_id": "V03",
      "canopy_area_m2": 301.2, "canopy_area_ha": 0.03012,
      "interrow_area_m2": 1900.4, "interrow_area_ha": 0.19004,
      "canopy_count": 412, "row_count": 15, "total_row_length_m": 690.5,
      "rows": [ { "row_id": "V03-R01", "length_m": 46.1 } ]
    }
  ]
}
```
We also call it with a single tile to fill the per-tile panel.

### `POST /v1/route`: farmer or auditor route
Request:
```json
{
  "role": "farmer",
  "tiles": ["siret3_r034_c025.tif"],
  "area_world": [[x,y], ...],
  "start_world": [x, y]
}
```
`role` ∈ `farmer` | `auditor`. `tiles`, `area_world` and `start_world` are all
optional. Without them: all tiles, no area filter, start from `start.geojson`.

Response:
```json
{
  "role": "farmer",
  "targets": [
    { "id": "gap-V03-R07", "kind": "inspection", "point_world": [x, y],
      "vineyard_id": "V03", "row_id": "V03-R07", "source_tile": "siret3_r034_c025.tif",
      "reason": "5.8 m gap in row" }
  ],
  "route": {
    "order": ["gap-V03-R07", "waste-..."],
    "length_m": 1963.0,
    "polyline_world": [[x, y], ...],
    "comparison": {
      "separate_trips_m": 9518.0, "optimized_tour_m": 1963.0,
      "saved_m": 7555.0, "reduction_pct": 79.4
    }
  },
  "message": null
}
```
- `kind`: `inspection` (row gap or missing plants), `waste` (collection), or
  `audit` (an auditor verification point; we will add its styling to the UI).
- `route: null` together with a human-readable `message` when there is no
  route. Cases: no targets in scope, too many targets (propose a scope),
  unreachable targets. The UI shows the message as-is.
- `polyline_world` starts and ends at the start point and must stay inside
  passages ∪ inter-row areas, outside forbidden ∪ canopies.

### Timeouts on our side
| Call | Our timeout |
|---|---|
| `/health` | 2 s |
| `/v1/detect` | 30 s × number of tiles (max 300 s) |
| `/v1/measurements` | 30 s |
| `/v1/route` | 120 s |

---

## 6. How to test against us

- Ground truth for accuracy: the 2 tiles in `examples/annotations.xml`.
- Our current numbers (classical CV) as a baseline to beat: inter-row IoU
  0.81–0.89, canopy area error ~2–20%, rows recovered 24/25 and 22/26, **no
  waste detection**, canopy instances approximate.
- Try the live app at https://geotech.amsoft.am. The current API is at
  https://api.geotech.amsoft.am/docs, and the shapes there (`/api/site/*`) are
  what the UI consumes today.
- Before handing over, run your container locally with the two volumes
  mounted and check:
  ```bash
  curl localhost:9090/health
  curl -X POST localhost:9090/v1/detect -H 'content-type: application/json' \
       -d '{"tiles":["siret3_r021_c012.tif"]}'
  curl -X POST localhost:9090/v1/route  -H 'content-type: application/json' \
       -d '{"role":"farmer"}'
  ```

---

## 7. Open questions for you (answer in your README)

1. How do you keep `vineyard_id` / `row_id` consistent across tiles:
   neighbour-tile stitching, a site-wide pass, or reuse of the organizer's
   block IDs?
2. What exactly is the auditor route's target logic, and how many targets per
   route?
3. Can inference fit in 1.5 GB RAM / CPU-only at ≤ 25 s per tile? If not,
   what is the minimum hardware?
4. Do measurements need the full neighbourhood of a tile (e.g. a row
   continuing into an undetected tile)? How do you report partial rows?
