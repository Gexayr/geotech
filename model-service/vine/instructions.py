"""Turn the planned route into human walking instructions (turn-by-turn, per leg between targets)."""
import math

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

DIRS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def _bearing(p, q):
    return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360      # 0 = north (grid north)


def _compass(b):
    return DIRS[int((b + 22.5) // 45) % 8]


def _turn(prev, cur):
    d = (cur - prev + 540) % 360 - 180
    if abs(d) < 25:
        return "continue straight"
    if abs(d) > 150:
        return "turn around"
    side = "right" if d > 0 else "left"
    return ("bear " if abs(d) < 60 else "turn ") + side


class Context:
    """What the walker is on: an inter-row of a block, an authorised path, or open ground."""

    def __init__(self, interrow, passages=None):
        self.ir = list(interrow.itertuples()) if interrow is not None and len(interrow) else []
        self.tree = STRtree([r.geometry for r in self.ir]) if self.ir else None
        self.passages = passages

    def where(self, seg):
        mid = seg.interpolate(0.5, normalized=True)
        if self.tree is not None:
            for j in self.tree.query(mid.buffer(0.6)):
                if self.ir[j].geometry.distance(mid) < 0.6:
                    return f"along an inter-row of block {self.ir[j].vineyard_id}"
        if self.passages is not None and self.passages.distance(mid) < 0.5:
            return "along the path"
        return "across open ground"


def _target_text(t):
    if t is None:
        return "the start point (route finished)"
    p = t["props"]
    if t["kind"] == "inspection":
        g = p.get("gap_m")
        return (f"inspection point {t['id']}: row gap of {g:.1f} m in row {p.get('row_id')} "
                f"(block {p.get('vineyard_id')}) - check for missing or dead vines")
    return f"waste {t['id']} (block {p.get('vineyard_id') or '-'}) - collect it"


def build_instructions(legs, targets, ctx, simplify_m=1.5, min_step_m=3.0):
    """legs: [{frm, to, coords}] from route.plan_route; targets: id -> {kind, props}."""
    out, total, heading = [], 0.0, None
    for k, leg in enumerate(legs, 1):
        line = LineString(leg["coords"]) if len(leg["coords"]) > 1 else None
        steps = []
        if line is not None and line.length > 0.5:
            pts = list(line.simplify(simplify_m).coords)
            # merge short / collinear pieces into steps
            raw = []
            for p, q in zip(pts[:-1], pts[1:]):
                seg = LineString([p, q])
                raw.append([_bearing(p, q), seg.length, seg])
            merged = []
            for b, l, seg in raw:
                if merged and (l < min_step_m or abs((b - merged[-1][0] + 540) % 360 - 180) < 25):
                    m = merged[-1]
                    m[2] = LineString(list(m[2].coords) + [seg.coords[-1]])
                    m[1] += l
                    if l > m[1] - l:                     # keep the dominant heading
                        m[0] = b
                else:
                    merged.append([b, l, seg])
            for b, l, seg in merged:
                verb = "Head" if heading is None else _turn(heading, b).capitalize()
                steps.append(dict(text=f"{verb} and walk {l:.0f} m {_compass(b)} {ctx.where(seg)}.",
                                  length_m=round(l, 1), coords=list(seg.coords)))
                heading = b
        L = sum(s["length_m"] for s in steps)
        total += L
        dest = targets.get(leg["to"]) if leg["to"] != "START" else None
        arrive = f"Arrive at {_target_text(dest)}."
        extra = [targets[c] for c in leg.get("covers", [])[1:] if c in targets]
        if extra:
            arrive += " Also here (within 2 m): " + "; ".join(_target_text(x) for x in extra) + "."
        out.append(dict(leg=k, frm=leg["frm"], to=leg["to"], length_m=round(L, 1),
                        cumulative_m=round(total, 1), steps=steps, arrive=arrive))
    return out
