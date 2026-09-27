import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { SiteMap } from "./components/SiteMap";
import { SiteLayerPanel } from "./components/SiteLayerPanel";
import { SiteMetricsPanel } from "./components/SiteMetricsPanel";
import { TileSelector } from "./components/TileSelector";
import { RouteStatus } from "./components/RouteStatus";
import { RowTable } from "./components/RowTable";
import { ExportModal } from "./components/ExportModal";
import { ConfirmModal } from "./components/ConfirmModal";
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
  Role,
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

// Remembered per browser so a refresh reopens the same tile (e.g. the one
// just uploaded) instead of jumping back to the first one in the list.
const SELECTED_TILE_KEY = "geotech.selectedTile";

function readSavedTile(): string | null {
  try {
    return localStorage.getItem(SELECTED_TILE_KEY);
  } catch {
    return null;
  }
}

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

  // Mobile only: the side pane becomes a bottom sheet over a full-screen map.
  const [sheetOpen, setSheetOpen] = useState(false);

  const [drawMode, setDrawMode] = useState(false);
  const [draftPoints, setDraftPoints] = useState<[number, number][]>([]);

  const [startMode, setStartMode] = useState(false);
  const [customStart, setCustomStart] = useState<[number, number] | null>(null);
  const [scopeTiles, setScopeTiles] = useState<string[]>([]);
  const [scopeAreaId, setScopeAreaId] = useState<string | null>(null);
  const [isCustomRoute, setIsCustomRoute] = useState(false);
  const [computingRoute, setComputingRoute] = useState(false);
  // Label for whatever slow server action is in flight (delete, compute…) —
  // shown as a spinner pill on the map so the user knows to wait.
  const [busy, setBusy] = useState<string | null>(null);
  // Bumped per route request so a slow, superseded answer (the model
  // service takes up to a minute) can't overwrite a newer one.
  const routeRequest = useRef(0);
  // A delete waiting for the user's OK in the confirm modal.
  const [pendingDelete, setPendingDelete] = useState<
    { kind: "tile" | "area"; id: string; label: string } | null
  >(null);
  const [lastComputeResult, setLastComputeResult] = useState<{
    found: boolean;
    message: string | null;
  } | null>(null);

  useEffect(() => {
    Promise.all([
      api.getRouteInfra(),
      api.listTiles(),
      // A route failure shouldn't take the tiles/map down with it.
      api.getRealRoute(role).catch((e) => {
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
        const saved = readSavedTile();
        const initial =
          tileList.find((t) => t.tile === saved) ??
          tileList.find((t) => t.annotated) ??
          tileList[0];
        if (initial) setSelectedTile(initial.tile);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const loadTileDetails = (tileName: string) => {
    setTileLayers(null);
    setTileMetrics(null);
    Promise.all([api.getTileLayers(tileName), api.getTileMetrics(tileName)])
      .then(([layers, metrics]) => {
        setTileLayers(layers);
        setTileMetrics(metrics);
      })
      .catch((e) => setError(String(e)));
  };

  useEffect(() => {
    if (!selectedTile) return;
    loadTileDetails(selectedTile);
    try {
      localStorage.setItem(SELECTED_TILE_KEY, selectedTile);
    } catch {
      // storage unavailable (private mode etc.) — just don't remember it
    }
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
    setSheetOpen(false); // free the map for tapping points (mobile)
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
    setBusy("Saving area…");
    try {
      const saved = await api.createCustomBlock(vineyardId, ring);
      setCustomBlocks((blocks) => [...blocks, saved]);
      setDrawMode(false);
      setDraftPoints([]);
    } finally {
      setBusy(null);
    }
  };

  const toggleStartMode = () => {
    if (!startMode) setSheetOpen(false);
    setStartMode(!startMode);
  };
  const onStartClick = (p: [number, number]) => {
    setCustomStart(p);
    setStartMode(false);
    setSheetOpen(true);
  };
  const resetStart = () => setCustomStart(null);
  const toggleTileScope = (tile: string) =>
    setScopeTiles((sel) => (sel.includes(tile) ? sel.filter((t) => t !== tile) : [...sel, tile]));

  const computeRoute = async () => {
    setComputingRoute(true);
    setBusy("Computing route…");
    try {
      const area = scopeAreaId
        ? customBlocks.find((b) => b.id === scopeAreaId)?.polygon
        : undefined;
      const id = ++routeRequest.current;
      const result = await api.computeCustomRoute({
        role,
        start: customStart ?? undefined,
        tiles: scopeTiles.length > 0 ? scopeTiles : undefined,
        area,
      });
      if (id !== routeRequest.current) return; // superseded (e.g. role switched)
      setRealRoute(result);
      setIsCustomRoute(true);
      setLastComputeResult({ found: result.route !== null, message: result.message });
    } catch (e) {
      setError(String(e));
    } finally {
      setComputingRoute(false);
      setBusy(null);
    }
  };

  const deleteCustomBlock = async (id: string) => {
    setBusy("Deleting area…");
    try {
      await api.deleteCustomBlock(id);
      setCustomBlocks((blocks) => blocks.filter((b) => b.id !== id));
      if (scopeAreaId === id) setScopeAreaId(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  // Farmer and auditor get different routes from the model service —
  // re-plan (same scope) whenever the role switches.
  const firstRoleRender = useRef(true);
  useEffect(() => {
    if (firstRoleRender.current) {
      firstRoleRender.current = false;
      return;
    }
    if (isCustomRoute) {
      computeRoute();
      return;
    }
    const id = ++routeRequest.current;
    setBusy(`Planning the ${role} route…`);
    api
      .getRealRoute(role)
      .then((route) => {
        if (id === routeRequest.current) setRealRoute(route);
      })
      .catch((e) => setError(String(e)))
      .finally(() => {
        if (id === routeRequest.current) setBusy(null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role]);

  const resetRouteToDefault = async () => {
    setCustomStart(null);
    setScopeTiles([]);
    setScopeAreaId(null);
    setIsCustomRoute(false);
    setLastComputeResult(null);
    setBusy("Resetting route…");
    try {
      const id = ++routeRequest.current;
      const route = await api.getRealRoute(role);
      if (id === routeRequest.current) setRealRoute(route);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const refreshTilesAfterUpload = async (uploadedFilenames: string[]) => {
    try {
      const [tileList, route] = await Promise.all([
        api.listTiles(),
        // New disrupted-row/waste targets from the upload feed the Targets
        // layer too — without this it only updates on a full page reload.
        api.getRealRoute(role).catch((e) => {
          console.error("Route refresh after upload failed:", e);
          return null;
        }),
      ]);
      setTiles(tileList);
      if (route) setRealRoute(route);
      // Select by the exact filenames just uploaded, not by re-deriving
      // "the uploaded tile" from a re-fetched (alphabetically sorted) list —
      // that broke as soon as more than one upload had ever happened.
      if (uploadedFilenames.length > 0) {
        const latest = uploadedFilenames[uploadedFilenames.length - 1];
        if (latest === selectedTile) {
          // Re-uploading the same filename overwrites its detection, but
          // `selectedTile` doesn't change value, so the effect watching it
          // won't re-fire on its own — load its (now different) data directly.
          loadTileDetails(latest);
        } else {
          setSelectedTile(latest);
        }
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const deleteTile = async (tileName: string) => {
    setBusy("Deleting tile…");
    try {
      await api.deleteTile(tileName);
      const [tileList, route] = await Promise.all([
        api.listTiles(),
        // The deleted tile's targets must drop out of the route too.
        api.getRealRoute(role).catch((e) => {
          console.error("Route refresh after delete failed:", e);
          return null;
        }),
      ]);
      setTiles(tileList);
      if (route) setRealRoute(route);
      const next = tileList.find((t) => t.annotated) ?? tileList[0] ?? null;
      if (!next) {
        setTileLayers(null);
        setTileMetrics(null);
      }
      setSelectedTile(next ? next.tile : null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
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
        {sheetOpen && <div className="sheet-backdrop" onClick={() => setSheetOpen(false)} />}
        <aside className={sheetOpen ? "side-pane side-pane--open" : "side-pane"}>
          <div className="sheet-header">
            <button
              className="sheet-handle"
              aria-label="Close controls"
              onClick={() => setSheetOpen(false)}
            />
            <span>Controls</span>
            <button className="sheet-close" aria-label="Close" onClick={() => setSheetOpen(false)}>
              ×
            </button>
          </div>

          <TileSelector
            tiles={tiles}
            selected={selectedTile}
            onSelect={setSelectedTile}
            onDelete={(tile) => setPendingDelete({ kind: "tile", id: tile, label: tile })}
            deleting={busy === "Deleting tile…"}
          />

          <UploadPanel onUploaded={refreshTilesAfterUpload} />

          <SiteLayerPanel visible={visible} onToggle={toggleLayer} />

          <SiteMetricsPanel blocks={tileMetrics?.blocks ?? null} />

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
            onDelete={(id) =>
              setPendingDelete({
                kind: "area",
                id,
                label: customBlocks.find((b) => b.id === id)?.vineyard_id ?? id,
              })
            }
          />

          {role === "auditor" && (
            <div className="panel">
              <h3>Auditor view</h3>
              <div className="route-note">
                Verify block/row counts and areas below, then export the audit trail.
              </div>
            </div>
          )}

          {firstBlockRows && firstBlockRows.length > 0 && (
            <details className="panel targets-details">
              <summary>Rows ({firstBlockRows.length})</summary>
              <div className="targets-scroll">
                <RowTable rows={firstBlockRows} activeRowId={activeRowId} onHoverRow={setActiveRowId} />
              </div>
            </details>
          )}

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
          {(busy || (!loaded && !error)) && (
            <div className="map-loading" role="status">
              <span className="spinner" />
              {busy ?? "Loading real site data…"}
            </div>
          )}
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
            areaFilter={
              scopeAreaId ? (customBlocks.find((b) => b.id === scopeAreaId)?.polygon ?? null) : null
            }
            drawMode={drawMode}
            draftPoints={draftPoints}
            onDrawClick={addDraftPoint}
            startMode={startMode}
            customStart={customStart}
            onStartClick={onStartClick}
          />
          {(drawMode || startMode) && (
            <div className="map-hint">
              {drawMode
                ? `Tap the map to add points · ${draftPoints.length}`
                : "Tap the map to set the start point"}
              <button
                onClick={() => {
                  setStartMode(false);
                  setSheetOpen(true);
                }}
              >
                {drawMode ? "Done" : "Cancel"}
              </button>
            </div>
          )}
          <button className="sheet-toggle" onClick={() => setSheetOpen(true)}>
            ☰ Controls
          </button>
          <StatBar blocks={tileMetrics?.blocks ?? null} route={realRoute?.route ?? null} />
        </div>
      </div>

      {pendingDelete && (
        <ConfirmModal
          title={pendingDelete.kind === "tile" ? "Delete tile?" : "Delete area?"}
          message={
            pendingDelete.kind === "tile"
              ? `${pendingDelete.label} — the GeoTIFF and its detections will be permanently removed from the server.`
              : `${pendingDelete.label} — this drawn area will be permanently removed.`
          }
          confirmLabel="Delete"
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => {
            const { kind, id } = pendingDelete;
            setPendingDelete(null);
            if (kind === "tile") deleteTile(id);
            else deleteCustomBlock(id);
          }}
        />
      )}
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
