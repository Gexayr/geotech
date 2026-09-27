# geotech-model: the model service (for geotech-app)

This service implements the agent brief's HTTP contract exactly: `/health`, `/v1/detect`,
`DELETE /v1/tiles/{name}`, `/v1/measurements` and `/v1/route` (role `farmer` | `auditor`).
Code: `service/app.py` (FastAPI) and `service/store.py` (cache, ID stitching, measurements, routes).
The algorithms live in `vine/`.

## Run

```bash
docker network create geotech            # once, shared with geotech-app
docker compose -f docker-compose.model.yml up -d --build
curl localhost:9090/health               # when published locally; in the network: http://geotech-model:9090
```

Without Docker:

```bash
pip install -r requirements.txt -r requirements-service.txt
TILES_DIR=/data/tiles ROUTE_DIR=/data/route CACHE_DIR=/data/cache uvicorn service.app:app --port 9090
```

**Volumes**
- `/data/tiles`: GeoTIFFs, read-only.
- `/data/route`: `start`, `passages`, `forbidden` GeoJSON, read-only.
- `/data/cache`: writable; holds one pickle per detected tile.

**Environment variables**
- `MODEL_WORKERS=1`: heavy jobs that may run at once.
- `ROUTE_SOLVE_S=30`: solver time limit per route.

## Measured performance

Measured locally on 10 real Sireț3 tiles (Windows 11 laptop, CPU only, one worker process):

| call | time | limit in brief |
|---|---|---|
| `/v1/detect`, 1 tile | **5.3 s** | ≤ 25 s |
| `/v1/detect`, 10 tiles | **19.8 s** (one was already cached) | ≤ 300 s |
| `/v1/measurements`, 10 tiles | < 1 s | 30 s |
| `/v1/route`, farmer (24 targets) | **41 s** | ≤ 60 s |
| `/v1/route`, auditor (12 targets) | **25 s** | ≤ 60 s |
| peak RAM | **≈ 480 MB** | ≤ 1.5 GB |

There are no neural weights. The default model is training-free classical CV: FFT row periodicity, CIVE + Otsu canopy, and row tracking. Parameters are in `model/vine_model_v1.json`. Because of this, ONNX or quantisation is not needed.

Accuracy on the 2 ground-truth tiles (challenge metric 0.6·IoU + 0.4·F1@0.5):

| tile | canopy score | canopy IoU | row recovery (F1) |
|---|---|---|---|
| r021_c012 | ≈ 0.59 | — | 1.00 |
| r006_c004 | ≈ 0.50 | 0.53 | 0.79 |

Reproduce with `python tools/benchmark.py` in the full repo. Waste is detected (colour and brightness anomalies); the ground-truth tiles contain 0 waste boxes.

## Answers to the open questions (brief §7)

1. **Consistent `vineyard_id` / `row_id` across tiles.**
   - Each tile is detected with **6 m of context from its neighbouring tiles** (when they are present), and the results are clipped to the tile.
   - A **site-wide stitching pass** then runs after every detect or delete:
     - Block pieces that touch across a tile edge (within 1.5 m) and have row directions within 6° are merged into one `vineyard_id` (union-find).
     - Row pieces of a block with the same across-row offset (within 0.4 × row spacing) become one `row_id`.
   - IDs are numbered north to south, then west to east. They can change when new tiles arrive (the brief allows this).
   - Tested: block V02 spans 6 tiles with continuous row numbers.
   - For the best site-wide IDs (the submission), use batch mode (point 5 below).
2. **Auditor target logic.** Per block in scope:
   - the **2 lowest-confidence rows** (to verify their length and structure);
   - **1 random sample row**, the median row by offset (a representative check);
   - the **lowest-confidence inter-row** (to verify its cover and area).

   Priority is 1 + 2 × (1 − confidence). A route has at most **40 targets**; when there are more, the highest-priority ones are kept.

   Farmer targets are the row gaps from `disrupted` rows / missing plants (priority 1 + gap / 10 m) and waste (priority 3).

   Both routes are prize-collecting TSPs: OR-Tools with a hard budget of ≤ 1.9 % off passages ∪ inter-rows, never through forbidden zones or rows. They return `comparison.separate_trips_m` (the sum of out-and-back trips from the start).

   More than 80 targets returns `route: null` with a message asking for a smaller scope. Unreachable targets are listed in `message`.
3. **1.5 GB RAM and CPU only.** Yes: the peak is about 480 MB and detection takes about 5 s per tile on a laptop CPU. A 4-core server will be similar.
4. **Partial rows.**
   - Rows are clipped to the tiles that are loaded. A row's length is the sum of its pieces in scope, merged by `row_id`. Areas are polygon unions.
   - A row that continues into a tile that has not been detected is reported with only the detected part. Its `row_id` stays the same once the neighbouring tile is detected, and it is then merged.
   - Detecting neighbours first gives the most accurate row ends, because detection uses their pixels as context.

## 5. Offline batch (CVAT 1.1 for Marcaj / submission)

```bash
python -m vine run /data/tiles --out /data/cache/batch --cvat --no-route --roles none
# -> /data/cache/batch/annotations.xml (CVAT for images 1.1, labels vineyard/row/interrow_area/waste)
```

## Objects: `confidence`

| object | how confidence is computed |
|---|---|
| **rows** | the share of the row axis covered by canopy, discounted for tree occlusion and shadow |
| **canopies** | the confidence of their row |
| **inter-rows** | the distance of the vegetation fraction from the class thresholds (0.25 / 0.75) |
| **waste** | from the cue type (vivid colour > bright) and the box size |
