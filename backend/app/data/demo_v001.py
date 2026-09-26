"""Self-consistent demo geometry for vineyard block V001.

This is illustrative data (no real drone imagery yet — see README for the
plan to swap this module for a real Marcaj export). Coordinates are in a
local planar metre grid, x = east, y = north, origin at the block's
south-west headland corner. Numbers are deliberately chosen so the
computed metrics land close to the worked example in the challenge brief
(canopy area ~139 m2, inter-row area 225 m2, block extent 720 m2, total
row length 120 m) — but everything below is *computed*, not hardcoded.
"""

from __future__ import annotations

BLOCK_ID = "V001"
BLOCK_NAME = "Block V001"

# --- Block extent -----------------------------------------------------
BLOCK_WIDTH_M = 18.0
BLOCK_LENGTH_M = 40.0
BLOCK_POLYGON = [
    (0.0, 0.0),
    (BLOCK_WIDTH_M, 0.0),
    (BLOCK_WIDTH_M, BLOCK_LENGTH_M),
    (0.0, BLOCK_LENGTH_M),
    (0.0, 0.0),
]

# --- Rows (vine-row axes) ---------------------------------------------
ROW_X = {"R01": 3.0, "R02": 8.0, "R03": 13.0, "R04": 17.0}
ROW_Y_START = 5.0
ROW_Y_END = 35.0  # -> 30 m per row, matches the brief's worked example

ROWS = [
    {
        "row_id": row_id,
        "vineyard_id": BLOCK_ID,
        "line": [(x, ROW_Y_START), (x, ROW_Y_END)],
    }
    for row_id, x in ROW_X.items()
]

# --- Inter-row passages --------------------------------------------
# One passage between each pair of adjacent rows -> 3 passages for 4 rows.
INTERROW_WIDTH_M = 2.5
_row_xs = sorted(ROW_X.values())
INTERROWS = []
for i in range(len(_row_xs) - 1):
    cx = (_row_xs[i] + _row_xs[i + 1]) / 2
    half_w = INTERROW_WIDTH_M / 2
    INTERROWS.append(
        {
            "interrow_id": f"IR0{i + 1}",
            "vineyard_id": BLOCK_ID,
            "center_x": cx,
            "polygon": [
                (cx - half_w, ROW_Y_START),
                (cx + half_w, ROW_Y_START),
                (cx + half_w, ROW_Y_END),
                (cx - half_w, ROW_Y_END),
                (cx - half_w, ROW_Y_START),
            ],
        }
    )

# --- Canopy polygons ----------------------------------------------
# 10 canopies per row, non-overlapping rectangles -> 40 total.
CANOPY_LENGTH_M = 2.4  # along the row (y)
CANOPY_WIDTH_M = 1.45  # across the row (x)
CANOPIES_PER_ROW = 10
CANOPY_SPACING_M = 3.0
CANOPY_FIRST_CENTER_Y = ROW_Y_START + CANOPY_LENGTH_M / 2 + 0.4  # small headland gap

CANOPIES = []
_counter = 1
for row_id, x in ROW_X.items():
    for i in range(CANOPIES_PER_ROW):
        cy = CANOPY_FIRST_CENTER_Y + i * CANOPY_SPACING_M
        hx, hy = CANOPY_WIDTH_M / 2, CANOPY_LENGTH_M / 2
        CANOPIES.append(
            {
                "canopy_id": f"C{_counter:03d}",
                "row_id": row_id,
                "vineyard_id": BLOCK_ID,
                "polygon": [
                    (x - hx, cy - hy),
                    (x + hx, cy - hy),
                    (x + hx, cy + hy),
                    (x - hx, cy + hy),
                    (x - hx, cy - hy),
                ],
            }
        )
        _counter += 1

# --- Waste objects ------------------------------------------------
WASTE = [
    {
        "waste_id": "W01",
        "vineyard_id": BLOCK_ID,
        "bbox": [(14.4, 7.4), (15.6, 7.4), (15.6, 8.6), (14.4, 8.6), (14.4, 7.4)],
    }
]

# --- Inspection targets ---------------------------------------------
TARGETS = [
    {"target_id": "T01", "kind": "inspect", "point": (5.5, 20.0)},
    {"target_id": "T02", "kind": "inspect", "point": (10.5, 30.0)},
]

START_ID = "start"
START_POINT = (9.0, -5.0)

# --- Walking network (organizer-defined passable paths) --------------
# Nodes: headland corners/junctions, passage entry points, targets, waste,
# and the organizer-defined start/end point. Edges: straight passable
# segments (interrow passages + headlands + the start connector). Route
# planning must stay on this graph so it never cuts through canopy rows.
NODES = {
    "start": START_POINT,
    "bh0": (0.0, 5.0),
    "bh_p1": (5.5, 5.0),
    "bh_mid": (9.0, 5.0),
    "bh_p2": (10.5, 5.0),
    "bh_p3": (15.0, 5.0),
    "bh1": (18.0, 5.0),
    "th0": (0.0, 35.0),
    "th_p1": (5.5, 35.0),
    "th_p2": (10.5, 35.0),
    "th_p3": (15.0, 35.0),
    "th1": (18.0, 35.0),
    "T01": (5.5, 20.0),
    "T02": (10.5, 30.0),
    "W01": (15.0, 8.0),
}

EDGES = [
    ("start", "bh_mid"),
    ("bh0", "bh_p1"),
    ("bh_p1", "bh_mid"),
    ("bh_mid", "bh_p2"),
    ("bh_p2", "bh_p3"),
    ("bh_p3", "bh1"),
    ("th0", "th_p1"),
    ("th_p1", "th_p2"),
    ("th_p2", "th_p3"),
    ("th_p3", "th1"),
    ("bh0", "th0"),
    ("bh1", "th1"),
    ("bh_p1", "T01"),
    ("T01", "th_p1"),
    ("bh_p2", "T02"),
    ("T02", "th_p2"),
    ("bh_p3", "W01"),
    ("W01", "th_p3"),
]
