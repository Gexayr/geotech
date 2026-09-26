import { useEffect, useState } from "react";
import { api } from "./api/client";
import { SiteMap } from "./components/SiteMap";
import { SiteLayerPanel } from "./components/SiteLayerPanel";
import { SiteMetricsPanel } from "./components/SiteMetricsPanel";
import { TileSelector } from "./components/TileSelector";
import { RouteStatus } from "./components/RouteStatus";
import { RowTable } from "./components/RowTable";
import { ExportModal } from "./components/ExportModal";
import { DrawBlockPanel } from "./components/DrawBlockPanel";
import { RoutePlanner } from "./components/RoutePlanner";
import { LayerChipBar } from "./components/LayerChipBar";
import { StatBar } from "./components/StatBar";
import { UploadPanel } from "./components/UploadPanel";
import type {
  RouteInfraResponse,
  TileSummary,
  TileLayersResponse,
  TileMetricsResponse,
  SiteLayerKey,
  RealRouteResponse,
  CustomBlock,
} from "./types";

const DEFAULT_VISIBILITY: Record<SiteLayerKey, boolean> = {
  studyArea: true,
  passages: true,
  forbidden: true,
  canopies: true,
  rows: true,
  interrows: true,
  waste: true,
  targets: true,
  route: true,
  customBlocks: true,
};

// The reference UX shows a whole neighbourhood at a glance rather than
// cropping tight to one 51x51m tile — pad the focus box out to at least this
// size (metres), centered on whatever's selected, so panning/zoom context
// (surrounding tiles, passages) stays visible.
const MIN_FOCUS_SIZE_M = 800;

function padBounds(
  bounds: [number, number, number, number],
  minSize: number
): [number, number, number, number] {
  const [x0, y0, x1, y1] = bounds;
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const halfW = Math.max((x1 - x0) / 2, minSize / 2);
  const halfH = Math.max((y1 - y0) / 2, minSize / 2);
  return [cx - halfW, cy - halfH, cx + halfW, cy + halfH];
}

type ExportKind = "annotations" | "route-infra" | null;
type Role = "farmer" | "auditor";

export default function App() {
  const [role, setRole] = useState<Role>("farmer");
  const [routeInfra, setRouteInfra] = useState<RouteInfraResponse | null>(null);
  const [tiles, setTiles] = useState<TileSummary[]>([]);
  const [selectedTile, setSelectedTile] = useState<string | null>(null);
  const [tileLayers, setTileLayers] = useState<TileLayersResponse | null>(null);
  const [tileMetrics, setTileMetrics] = useState<TileMetricsResponse | null>(null);
  const [realRoute, setRealRoute] = useState<RealRouteResponse | null>(null);
  const [customBlocks, setCustomBlocks] = useState<CustomBlock[]>([]);
  const [visible, setVisible] = useState(DEFAULT_VISIBILITY);
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState<ExportKind>(null);
  const [error, setError] = useState<string | null>(null);

  const [drawMode, setDrawMode] = useState(false);
  const [draftPoints, setDraftPoints] = useState<[number, number][]>([]);

  const [startMode, setStartMode] = useState(false);
  const [customStart, setCustomStart] = useState<[number, number] | null>(null);
  const [scopeTiles, setScopeTiles] = useState<string[]>([]);
  const [scopeAreaId, setScopeAreaId] = useState<string | null>(null);
  const [isCustomRoute, setIsCustomRoute] = useState(false);
  const [computingRoute, setComputingRoute] = useState(false);
  const [lastComputeResult, setLastComputeResult] = useState<{
    found: boolean;
    message: string | null;
  } | null>(null);

  useEffect(() => {
    Promise.all([
      api.getRouteInfra(),
      api.listTiles(),
      // A route failure shouldn't take the tiles/map down with it.
      api.getRealRoute().catch((e) => {
        console.error("Route unavailable:", e);
        return null;
      }),
      api.listCustomBlocks(),
    ])
      .then(([infra, tileList, route, blocks]) => {
        setRouteInfra(infra);
        setTiles(tileList);
        setRealRoute(route);
        setCustomBlocks(blocks);
        const firstAnnotated = tileList.find((t) => t.annotated) ?? tileList[0];
        if (firstAnnotated) setSelectedTile(firstAnnotated.tile);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selectedTile) return;
    setTileLayers(null);
    setTileMetrics(null);
    Promise.all([api.getTileLayers(selectedTile), api.getTileMetrics(selectedTile)])
      .then(([layers, metrics]) => {
        setTileLayers(layers);
        setTileMetrics(metrics);
      })
      .catch((e) => setError(String(e)));
  }, [selectedTile]);

  const toggleLayer = (key: SiteLayerKey) => setVisible((v) => ({ ...v, [key]: !v[key] }));
  const selectedTileInfo = tiles.find((t) => t.tile === selectedTile) ?? null;
  const firstBlockRows = tileMetrics?.blocks[0]?.rows ?? null;
  // With no tiles yet (fresh server, nothing uploaded) there's no tile to
  // fetch layers for — the initial load is complete once the site data is in.
  const loaded = routeInfra && (!selectedTile || (tileLayers && tileMetrics));
  const focusBounds = selectedTileInfo ? padBounds(selectedTileInfo.bounds, MIN_FOCUS_SIZE_M) : null;

  const startDraw = () => {
    setDraftPoints([]);
    setDrawMode(true);
  };
  const cancelDraw = () => {
    setDrawMode(false);
    setDraftPoints([]);
  };
  const undoPoint = () => setDraftPoints((pts) => pts.slice(0, -1));
  const addDraftPoint = (p: [number, number]) => {
    if (!drawMode) return;
    setDraftPoints((pts) => [...pts, p]);
  };
  const saveDraft = async (vineyardId: string) => {
    const ring = [...draftPoints, draftPoints[0]];
    const saved = await api.createCustomBlock(vineyardId, ring);
    setCustomBlocks((blocks) => [...blocks, saved]);
    setDrawMode(false);
    setDraftPoints([]);
  };

  const toggleStartMode = () => setStartMode((v) => !v);
  const onStartClick = (p: [number, number]) => {
    setCustomStart(p);
    setStartMode(false);
  };
  const resetStart = () => setCustomStart(null);
  const toggleTileScope = (tile: string) =>
    setScopeTiles((sel) => (sel.includes(tile) ? sel.filter((t) => t !== tile) : [...sel, tile]));

  const computeRoute = async () => {
    setComputingRoute(true);
    try {
      const area = scopeAreaId
        ? customBlocks.find((b) => b.id === scopeAreaId)?.polygon
        : undefined;
      const result = await api.computeCustomRoute({
        start: customStart ?? undefined,
        tiles: scopeTiles.length > 0 ? scopeTiles : undefined,
        area,
      });
      setRealRoute(result);
      setIsCustomRoute(true);
      setLastComputeResult({ found: result.route !== null, message: result.message });
    } catch (e) {
      setError(String(e));
    } finally {
      setComputingRoute(false);
    }
  };

  const deleteCustomBlock = async (id: string) => {
    await api.deleteCustomBlock(id);
    setCustomBlocks((blocks) => blocks.filter((b) => b.id !== id));
    if (scopeAreaId === id) setScopeAreaId(null);
  };

  const resetRouteToDefault = async () => {
    setCustomStart(null);
    setScopeTiles([]);
    setScopeAreaId(null);
    setIsCustomRoute(false);
    setLastComputeResult(null);
    try {
      setRealRoute(await api.getRealRoute());
    } catch (e) {
      setError(String(e));
    }
  };

  const refreshTilesAfterUpload = async () => {
    try {
      const tileList = await api.listTiles();
      setTiles(tileList);
      const uploaded = tileList.filter((t) => t.source === "uploaded");
      if (uploaded.length > 0) setSelectedTile(uploaded[uploaded.length - 1].tile);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <span className="title">Vineyard AI / Field planner</span>
        <span className="badge">REAL DATA — Sireț3, EPSG:32635</span>
        <div className="role-switch">
          <button className={role === "farmer" ? "active" : ""} onClick={() => setRole("farmer")}>
            Farmer
          </button>
          <button
            className={role === "auditor" ? "active" : ""}
            onClick={() => setRole("auditor")}
          >
            Auditor
          </button>
        </div>
      </header>

      {error && <div className="error-banner">Failed to reach backend API: {error}</div>}

      <div className="content">
        <aside className="side-pane">
          <TileSelector tiles={tiles} selected={selectedTile} onSelect={setSelectedTile} />

          <UploadPanel onUploaded={refreshTilesAfterUpload} />

          <SiteLayerPanel visible={visible} onToggle={toggleLayer} />

          <SiteMetricsPanel blocks={tileMetrics?.blocks ?? null} />

          {role === "farmer" ? (
            <>
              <RouteStatus routeInfra={routeInfra} realRoute={realRoute} />

              <RoutePlanner
                annotatedTiles={tiles.filter((t) => t.source === "ground_truth")}
                customBlocks={customBlocks}
                customStart={customStart}
                startMode={startMode}
                onToggleStartMode={toggleStartMode}
                onResetStart={resetStart}
                selectedTileScope={scopeTiles}
                onToggleTileScope={toggleTileScope}
                selectedAreaId={scopeAreaId}
                onSelectArea={setScopeAreaId}
                onCompute={computeRoute}
                onResetToDefault={resetRouteToDefault}
                isCustomRoute={isCustomRoute}
                computing={computingRoute}
                lastResult={lastComputeResult}
              />

              <DrawBlockPanel
                drawMode={drawMode}
                pointCount={draftPoints.length}
                onStart={startDraw}
                onUndo={undoPoint}
                onCancel={cancelDraw}
                onSave={saveDraft}
                customBlocks={customBlocks}
                onDelete={deleteCustomBlock}
              />
            </>
          ) : (
            <div className="panel">
              <h3>Auditor view</h3>
              <div className="route-note">
                Route planning is hidden in this view — verify block/row counts and areas
                below, then export the audit trail.
              </div>
            </div>
          )}

          <div className="panel">
            <h3>Rows</h3>
            <RowTable rows={firstBlockRows} activeRowId={activeRowId} onHoverRow={setActiveRowId} />
          </div>

          {role === "auditor" ? (
            <>
              <div className="export-buttons">
                <button
                  className="btn btn--primary"
                  disabled={!tileLayers}
                  onClick={() => setExportOpen("annotations")}
                >
                  View annotation export
                </button>
                <a
                  className="btn btn--ghost"
                  style={{ textAlign: "center", textDecoration: "none" }}
                  href={api.measurementsCsvUrl()}
                  download="measurements.csv"
                >
                  Download measurements.csv
                </a>
              </div>
            </>
          ) : (
            <>
              <div className="export-buttons">
                <button
                  className="btn btn--primary"
                  disabled={!tileLayers}
                  onClick={() => setExportOpen("annotations")}
                >
                  View annotation export
                </button>
                <button
                  className="btn btn--ghost"
                  disabled={!routeInfra}
                  onClick={() => setExportOpen("route-infra")}
                >
                  View route infrastructure
                </button>
              </div>
              <div className="export-buttons">
                <a
                  className="btn btn--primary"
                  style={{
                    textAlign: "center",
                    textDecoration: "none",
                    pointerEvents: realRoute?.route ? "auto" : "none",
                    opacity: realRoute?.route ? 1 : 0.5,
                  }}
                  href={api.routeGeojsonUrl()}
                  download="route.geojson"
                >
                  Download route.geojson
                </a>
                <a
                  className="btn btn--ghost"
                  style={{ textAlign: "center", textDecoration: "none" }}
                  href={api.measurementsCsvUrl()}
                  download="measurements.csv"
                >
                  Download measurements.csv
                </a>
              </div>
            </>
          )}
        </aside>

        <div className="map-pane">
          {!loaded && !error && <div className="map-loading">Loading real site data…</div>}
          <LayerChipBar visible={visible} onToggle={toggleLayer} />
          <SiteMap
            routeInfra={routeInfra}
            tiles={tiles}
            selectedTile={selectedTile}
            onSelectTile={setSelectedTile}
            tileLayers={tileLayers}
            visible={visible}
            highlightRowId={activeRowId}
            focusBounds={focusBounds}
            realRoute={realRoute}
            customBlocks={customBlocks}
            drawMode={drawMode}
            draftPoints={draftPoints}
            onDrawClick={addDraftPoint}
            startMode={startMode}
            customStart={customStart}
            onStartClick={onStartClick}
          />
          <StatBar blocks={tileMetrics?.blocks ?? null} route={realRoute?.route ?? null} />
        </div>
      </div>

      {exportOpen === "annotations" && tileLayers && (
        <ExportModal
          title={`Real annotations — ${selectedTile}`}
          filename={`${selectedTile}_annotations.geojson.json`}
          data={{ tile: selectedTile, layers: tileLayers, metrics: tileMetrics }}
          onClose={() => setExportOpen(null)}
        />
      )}
      {exportOpen === "route-infra" && routeInfra && (
        <ExportModal
          title="Route infrastructure (organizer-supplied, real)"
          filename="route_infra.json"
          data={routeInfra}
          onClose={() => setExportOpen(null)}
        />
      )}
    </div>
  );
}
