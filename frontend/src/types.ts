export interface BlockSummary {
  id: string;
  name: string;
  extent_m2: number;
}

export interface GeoFeature {
  type: "Feature";
  geometry: { type: string; coordinates: any };
  properties: Record<string, any>;
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
}

export interface LayersResponse {
  canopies: FeatureCollection;
  rows: FeatureCollection;
  interrows: FeatureCollection;
  waste: FeatureCollection;
  targets: FeatureCollection;
}

export interface RowMeasurement {
  row_id: string;
  length_m: number;
  block_id: string;
}

export interface MetricsResponse {
  block_id: string;
  canopy_area_m2: number;
  canopy_area_ha: number;
  interrow_area_m2: number;
  interrow_area_ha: number;
  block_extent_m2: number;
  row_count: number;
  canopy_count: number;
  total_row_length_m: number;
  rows: RowMeasurement[];
}

export interface RouteComparison {
  separate_trips_m: number;
  optimized_tour_m: number;
  saved_m: number;
  reduction_pct: number;
}

export interface RouteResponse {
  block_id: string;
  start_id: string;
  start_point: [number, number];
  order: string[];
  targets_visited: number;
  targets_total: number;
  length_m: number;
  polyline: [number, number][];
  comparison: RouteComparison;
}

export type LayerKey = "canopies" | "rows" | "interrows" | "waste" | "targets" | "route";

// --- Real-data endpoints (app/api/site.py) -------------------------------

export interface RouteInfraResponse {
  study_area: FeatureCollection;
  passages: FeatureCollection;
  forbidden: FeatureCollection;
  start: FeatureCollection;
  extent: [number, number];
}

export interface TileSummary {
  tile: string;
  bounds: [number, number, number, number]; // [x0, y0, x1, y1] local metres
  vineyard_ids: string[];
  annotated: boolean;
  source: "ground_truth" | "classical_cv" | "uploaded" | "model" | null;
}

export interface CustomBlock {
  id: string;
  vineyard_id: string;
  polygon: [number, number][]; // local metres, closed ring
  created_at: number;
}

export interface TileLayersResponse {
  canopies: FeatureCollection;
  rows: FeatureCollection;
  interrows: FeatureCollection;
  waste: FeatureCollection;
}

export interface TileBlockMetrics {
  vineyard_id: string;
  canopy_area_m2: number;
  canopy_area_ha: number;
  interrow_area_m2: number;
  interrow_area_ha: number;
  canopy_count: number;
  row_count: number;
  total_row_length_m: number;
  rows: { row_id: string; length_m: number }[];
}

export interface TileMetricsResponse {
  tile: string;
  blocks: TileBlockMetrics[];
}

export type SiteLayerKey =
  | "canopies"
  | "rows"
  | "interrows"
  | "waste"
  | "studyArea"
  | "passages"
  | "forbidden"
  | "targets"
  | "route"
  | "customBlocks";

export type Role = "farmer" | "auditor";

export interface RealTarget {
  id: string;
  // "audit" = an auditor verification point from the model service
  kind: "inspection" | "waste" | "audit";
  point: [number, number];
  vineyard_id: string | null;
  row_id: string | null;
  source_tile: string;
  reason?: string; // model service: why this is a target
}

export interface RealRoute {
  order: string[];
  length_m: number;
  polyline_local: [number, number][];
  polyline_world: [number, number][];
  comparison: RouteComparison;
}

export interface RealRouteResponse {
  targets: RealTarget[];
  route: RealRoute | null;
  message: string | null;
}
