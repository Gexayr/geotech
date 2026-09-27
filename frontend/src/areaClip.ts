import * as polygonClippingNs from "polygon-clipping";
import type { GeoFeature, TileLayersResponse } from "./types";

// The package's ESM build only has a default export, while its typings
// declare named ones — take whichever the bundler actually hands us.
const polygonClipping: typeof polygonClippingNs =
  (polygonClippingNs as any).default ?? polygonClippingNs;

type Pt = [number, number];
type BBox = [number, number, number, number];

/** A hand-drawn area (local metres, closed ring) plus its bbox for cheap
 * rejects — build once with makeArea() and reuse for every feature. */
export interface ClipArea {
  ring: Pt[];
  bbox: BBox;
}

function bboxOf(points: Pt[]): BBox {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of points) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}

const disjoint = (a: BBox, b: BBox) => a[2] < b[0] || a[0] > b[2] || a[3] < b[1] || a[1] > b[3];

export function makeArea(ring: Pt[]): ClipArea {
  return { ring, bbox: bboxOf(ring) };
}

export function pointInArea([x, y]: Pt, area: ClipArea): boolean {
  const b = area.bbox;
  if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) return false;
  const ring = area.ring;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function clipPolygonFeature(f: GeoFeature, area: ClipArea): GeoFeature | null {
  const { type, coordinates } = f.geometry;
  const rings: Pt[] = type === "Polygon" ? coordinates.flat() : coordinates.flat(2);
  if (rings.length === 0 || disjoint(bboxOf(rings), area.bbox)) return null;
  const clipped = polygonClipping.intersection(coordinates, [area.ring]);
  if (clipped.length === 0) return null;
  return { ...f, geometry: { type: "MultiPolygon", coordinates: clipped } };
}

/** Splits each segment at its crossings with the area boundary and keeps
 * the pieces whose midpoint is inside — exact for any (concave) area. */
function clipLine(line: Pt[], area: ClipArea): Pt[][] {
  const out: Pt[][] = [];
  let current: Pt[] = [];
  const ring = area.ring;

  for (let s = 0; s < line.length - 1; s++) {
    const [ax, ay] = line[s];
    const [bx, by] = line[s + 1];
    const ts = [0, 1];
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [cx, cy] = ring[j];
      const [dx, dy] = ring[i];
      const denom = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx);
      if (denom === 0) continue;
      const t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / denom;
      const u = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / denom;
      if (t > 0 && t < 1 && u >= 0 && u <= 1) ts.push(t);
    }
    ts.sort((a, b) => a - b);
    const at = (t: number): Pt => [ax + (bx - ax) * t, ay + (by - ay) * t];

    for (let k = 0; k < ts.length - 1; k++) {
      const t0 = ts[k];
      const t1 = ts[k + 1];
      if (t1 - t0 < 1e-12) continue;
      if (pointInArea(at((t0 + t1) / 2), area)) {
        if (current.length === 0) current.push(at(t0));
        current.push(at(t1));
      } else if (current.length > 0) {
        out.push(current);
        current = [];
      }
    }
  }
  if (current.length > 1) out.push(current);
  return out;
}

function clipLineFeatures(features: GeoFeature[], area: ClipArea): GeoFeature[] {
  const out: GeoFeature[] = [];
  for (const f of features) {
    const line = f.geometry.coordinates as Pt[];
    if (line.length < 2 || disjoint(bboxOf(line), area.bbox)) continue;
    for (const piece of clipLine(line, area)) {
      out.push({ ...f, geometry: { type: "LineString", coordinates: piece } });
    }
  }
  return out;
}

function clipPolygons(features: GeoFeature[], area: ClipArea): GeoFeature[] {
  const out: GeoFeature[] = [];
  for (const f of features) {
    const c = clipPolygonFeature(f, area);
    if (c) out.push(c);
  }
  return out;
}

/** A tile's detection layers cut to the area — only what lies inside it
 * is drawn, with canopies/inter-rows/rows crossing the edge trimmed. */
export function clipLayers(layers: TileLayersResponse, area: ClipArea): TileLayersResponse {
  return {
    canopies: { ...layers.canopies, features: clipPolygons(layers.canopies.features, area) },
    interrows: { ...layers.interrows, features: clipPolygons(layers.interrows.features, area) },
    waste: { ...layers.waste, features: clipPolygons(layers.waste.features, area) },
    rows: { ...layers.rows, features: clipLineFeatures(layers.rows.features, area) },
  };
}
