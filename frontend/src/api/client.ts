import type {
  BlockSummary,
  LayersResponse,
  MetricsResponse,
  RouteResponse,
  RouteInfraResponse,
  TileSummary,
  TileLayersResponse,
  TileMetricsResponse,
  RealRouteResponse,
  CustomBlock,
} from "../types";

// "" = same origin — correct for the single-container Docker build, where
// the backend serves this build's static files itself (see app/main.py).
// Local dev (`vite dev`, separate port from uvicorn) overrides this via
// VITE_API_URL in .env.development.
const BASE_URL = import.meta.env.VITE_API_URL ?? "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`Request to ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // Demo endpoints (synthetic V001 block) — kept for reference, no longer
  // used by the main app view now that real data is available.
  listBlocks: () => getJson<BlockSummary[]>("/api/blocks"),
  getLayers: (blockId: string) => getJson<LayersResponse>(`/api/blocks/${blockId}/layers`),
  getMetrics: (blockId: string) => getJson<MetricsResponse>(`/api/blocks/${blockId}/metrics`),
  getRoute: (blockId: string) => getJson<RouteResponse>(`/api/blocks/${blockId}/route`),

  // Real data: organizer-supplied route infrastructure + the example tiles
  // with their real Marcaj (CVAT) annotations.
  getRouteInfra: () => getJson<RouteInfraResponse>("/api/site/route-infra"),
  listTiles: () => getJson<TileSummary[]>("/api/site/tiles"),
  getTileLayers: (tile: string) =>
    getJson<TileLayersResponse>(`/api/site/tiles/${encodeURIComponent(tile)}/layers`),
  getTileMetrics: (tile: string) =>
    getJson<TileMetricsResponse>(`/api/site/tiles/${encodeURIComponent(tile)}/metrics`),
  tileImageUrl: (tile: string) =>
    `${BASE_URL}/api/site/tiles/${encodeURIComponent(tile)}/image.jpg`,
  getRealRoute: () => getJson<RealRouteResponse>("/api/site/route"),
  // For turning a relative path the backend returned (e.g. an upload's
  // annotation_xml_url) into a fetchable/clickable absolute URL.
  apiUrl: (path: string) => `${BASE_URL}${path}`,
  uploadTiles: async (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const res = await fetch(`${BASE_URL}/api/site/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json() as Promise<{
      results: { filename: string; ok: boolean; error?: string; canopies?: number; rows?: number; interrows?: number }[];
      annotation_xml_url: string | null;
    }>;
  },
  computeCustomRoute: async (params: {
    start?: [number, number];
    tiles?: string[];
    area?: [number, number][];
  }) => {
    const res = await fetch(`${BASE_URL}/api/site/route/custom`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw new Error(`Failed to compute route: ${res.status}`);
    return res.json() as Promise<RealRouteResponse>;
  },
  routeGeojsonUrl: () => `${BASE_URL}/api/site/route.geojson`,
  measurementsCsvUrl: () => `${BASE_URL}/api/site/measurements.csv`,

  listCustomBlocks: () => getJson<CustomBlock[]>("/api/site/custom-blocks"),
  createCustomBlock: async (vineyardId: string, polygon: [number, number][]) => {
    const res = await fetch(`${BASE_URL}/api/site/custom-blocks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vineyard_id: vineyardId, polygon }),
    });
    if (!res.ok) throw new Error(`Failed to save block: ${res.status}`);
    return res.json() as Promise<CustomBlock>;
  },
  deleteCustomBlock: async (id: string) => {
    const res = await fetch(`${BASE_URL}/api/site/custom-blocks/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error(`Failed to delete block: ${res.status}`);
  },
};
