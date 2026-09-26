"""Batch runner: classical-CV pre-annotation for all 311 Sireț3 tiles.

No trained model, no GPU — this is a stopgap in case the team's real model
isn't ready in time, calibrated against the 2 tiles we have real ground
truth for. Honest about its limits:

- Canopy AREA (pixel-level union) is decent (~0.5-0.6 IoU against ground
  truth on the 2 known tiles).
- Individual canopy INSTANCES are approximate: split at a fixed 1.2 m
  interval along each detected row (the annotation rules' own fallback
  for "no visible narrowing"), not real per-plant boundaries.
- Row axes are good: dominant angle via Hough, row positions via a
  smoothed projection profile — recovered 24/25 real rows with a matching
  spacing pattern on the calibration tile.
- Waste: not attempted at all (0 boxes always) — unreliable without a
  trained detector, and a false box costs as much as a missed one.
- Non-vineyard tiles (village, roads) are detected via a weak-signal check
  (Hough vote count, vegetation-fraction band) and left empty, since false
  canopies on empty tiles are penalised in scoring.

The detection lives in app/services/classical_cv.py and the CVAT XML
serialisation in app/services/cvat_export.py — both shared with the live
single-tile upload path (api/site.py's /upload, which writes one small XML
per upload instead of this script's one-big-file-for-everything). This
script just loops detection over every tile in app/data/real/tiles/ and
writes the combined result to app/data/real/auto_annotations/annotations.xml.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make `app` importable when run directly (`python scripts/classical_pre_annotate.py`).
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import classical_cv, cvat_export  # noqa: E402

TILES_DIR = Path(__file__).parent.parent / "app" / "data" / "real" / "tiles"
OUT_DIR = Path(__file__).parent.parent / "app" / "data" / "real" / "auto_annotations"
OUT_PATH = OUT_DIR / "annotations.xml"


def main():
    tile_paths = sorted(TILES_DIR.glob("*.tif"))
    print(f"Processing {len(tile_paths)} tiles...")
    results = {}
    t0 = time.time()
    n_with_content = 0
    for i, path in enumerate(tile_paths):
        r = classical_cv.process_tile(path)
        results[path.name] = r
        if r["rows"]:
            n_with_content += 1
        if (i + 1) % 25 == 0 or (i + 1) == len(tile_paths):
            elapsed = time.time() - t0
            print(
                f"  {i + 1}/{len(tile_paths)} tiles, {elapsed:.1f}s elapsed, "
                f"{n_with_content} with detected rows so far",
                file=sys.stderr,
            )

    total_rows = sum(len(r["rows"]) for r in results.values())
    total_canopies = sum(len(r["canopies"]) for r in results.values())
    total_interrows = sum(len(r["interrows"]) for r in results.values())
    print(f"Done in {time.time()-t0:.1f}s. Tiles with content: {n_with_content}/{len(tile_paths)}")
    print(f"Total rows={total_rows} canopies={total_canopies} interrows={total_interrows}")

    cvat_export.write_cvat_xml(results, OUT_PATH)
    print(f"Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
