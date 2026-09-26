"""Serialises classical_cv.process_tile() results (pixel-space) into a real
CVAT for images 1.1 XML — the same format Marcaj itself uses (see
scripts/classical_pre_annotate.py's docstring). Shared by:
- the offline batch script (one XML for all 311 tiles), and
- the live upload endpoint (one XML per upload request, covering only the
  tile(s) just uploaded — not the whole catalog).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.dom import minidom


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


def to_pretty_string(root: ET.Element) -> str:
    return minidom.parseString(ET.tostring(root)).toprettyxml(indent=" ")


def write_cvat_xml(tile_results: dict[str, dict], path) -> None:
    root = build_cvat_xml(tile_results)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_pretty_string(root), encoding="utf-8")
