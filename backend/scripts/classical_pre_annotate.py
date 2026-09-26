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

The detection itself lives in app/services/classical_cv.py — shared with
the live single-tile upload path (api/site.py's /upload). This script just
loops it over every tile in app/data/real/tiles/ and writes one CVAT XML to
app/data/real/auto_annotations/annotations.xml.
"""

from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

# Make `app` importable when run directly (`python scripts/classical_pre_annotate.py`).
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import classical_cv  # noqa: E402

TILES_DIR = Path(__file__).parent.parent / "app" / "data" / "real" / "tiles"
OUT_DIR = Path(__file__).parent.parent / "app" / "data" / "real" / "auto_annotations"
OUT_PATH = OUT_DIR / "annotations.xml"


def build_cvat_xml(tile_results: dict[str, dict]) -> ET.Element:
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"
    meta = ET.SubElement(root, "meta")
    task = ET.SubElement(meta, "task")
    ET.SubElement(task, "name").text = "Sireț3 — classical-CV fallback pre-annotation"
    labels = ET.SubElement(task, "labels")

    def add_label(name, geom_type, attrs):
        lbl = ET.SubElement(labels, "label")
        ET.SubElement(lbl, "name").text = name
        ET.SubElement(lbl, "type").text = geom_type
        attrs_el = ET.SubElement(lbl, "attributes")
        for aname, values in attrs:
            a = ET.SubElement(attrs_el, "attribute")
            ET.SubElement(a, "name").text = aname
            ET.SubElement(a, "mutable").text = "False"
            ET.SubElement(a, "input_type").text = "select" if values else "text"
            ET.SubElement(a, "default_value").text = values[0] if values else ""
            ET.SubElement(a, "values").text = "\n".join(values) if values else ""

    add_label("vineyard", "polygon", [("vineyard_id", [])])
    add_label("waste", "rectangle", [("vineyard_id", [])])
    add_label(
        "row",
        "polyline",
        [
            ("vineyard_id", []),
            ("row_id", []),
            ("row_structure", ["regular", "disrupted", "unassessable"]),
        ],
    )
    add_label(
        "interrow_area",
        "polygon",
        [
            ("vineyard_id", []),
            ("interrow_cover", ["bare_soil", "vegetation", "mixed", "unassessable"]),
        ],
    )

    for img_id, (tile_name, result) in enumerate(tile_results.items()):
        image_el = ET.SubElement(root, "image")
        image_el.set("id", str(img_id))
        image_el.set("name", tile_name)
        image_el.set("width", "2048")
        image_el.set("height", "2048")

        for row in result["rows"]:
            pts = ";".join(f"{x:.1f},{y:.1f}" for x, y in row["points"])
            pl = ET.SubElement(image_el, "polyline")
            pl.set("label", "row")
            pl.set("points", pts)
            pl.set("occluded", "0")
            for aname in ("vineyard_id", "row_id", "row_structure"):
                a = ET.SubElement(pl, "attribute")
                a.set("name", aname)
                a.text = row[aname]

        for c in result["canopies"]:
            pts = ";".join(f"{x:.1f},{y:.1f}" for x, y in c["points"])
            pg = ET.SubElement(image_el, "polygon")
            pg.set("label", "vineyard")
            pg.set("points", pts)
            pg.set("occluded", "0")
            a = ET.SubElement(pg, "attribute")
            a.set("name", "vineyard_id")
            a.text = c["vineyard_id"]

        for ir in result["interrows"]:
            pts = ";".join(f"{x:.1f},{y:.1f}" for x, y in ir["points"])
            pg = ET.SubElement(image_el, "polygon")
            pg.set("label", "interrow_area")
            pg.set("points", pts)
            pg.set("occluded", "0")
            for aname in ("vineyard_id", "interrow_cover"):
                a = ET.SubElement(pg, "attribute")
                a.set("name", aname)
                a.text = ir[aname]

    return root


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

    root = build_cvat_xml(results)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent=" ")
    OUT_PATH.write_text(xml_str, encoding="utf-8")
    print(f"Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
