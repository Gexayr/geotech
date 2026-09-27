import type { TileSummary, CustomBlock } from "../types";

interface Props {
  annotatedTiles: TileSummary[];
  customBlocks: CustomBlock[];
  customStart: [number, number] | null;
  startMode: boolean;
  onToggleStartMode: () => void;
  onResetStart: () => void;
  selectedTileScope: string[];
  onToggleTileScope: (tileName: string) => void;
  selectedAreaId: string | null;
  onSelectArea: (id: string | null) => void;
  onCompute: () => void;
  onResetToDefault: () => void;
  isCustomRoute: boolean;
  computing: boolean;
  lastResult: { found: boolean; message: string | null } | null;
}

export function RoutePlanner({
  annotatedTiles,
  customBlocks,
  customStart,
  startMode,
  onToggleStartMode,
  onResetStart,
  selectedTileScope,
  onToggleTileScope,
  selectedAreaId,
  onSelectArea,
  onCompute,
  onResetToDefault,
  isCustomRoute,
  computing,
  lastResult,
}: Props) {
  return (
    <div className="panel">
      <h3>Plan a route</h3>

      <div className="route-note" style={{ marginBottom: 6 }}>
        Start point
      </div>
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <button
          className={startMode ? "btn btn--primary" : "btn btn--ghost"}
          onClick={onToggleStartMode}
        >
          {startMode ? "Click the map…" : "Set start point"}
        </button>
        {customStart && (
          <button className="btn btn--ghost" onClick={onResetStart}>
            Use organizer's
          </button>
        )}
      </div>
      {customStart && (
        <div className="route-note" style={{ marginBottom: 8 }}>
          Custom start: ({customStart[0].toFixed(1)}, {customStart[1].toFixed(1)})
        </div>
      )}

      {annotatedTiles.length > 0 && (
        <>
          <div className="route-note" style={{ marginBottom: 4 }}>
            Scope — ground-truth tiles to visit (empty = every annotated tile,
            currently 744+ targets — draw an area below to scope the ~230
            auto-CV tiles instead, listing them all here wouldn't fit)
          </div>
          <ul className="layer-list" style={{ marginBottom: 8 }}>
            {annotatedTiles.map((t) => (
              <li key={t.tile}>
                <label>
                  <input
                    type="checkbox"
                    checked={selectedTileScope.includes(t.tile)}
                    onChange={() => onToggleTileScope(t.tile)}
                  />
                  <span>{t.tile}</span>
                </label>
              </li>
            ))}
          </ul>
        </>
      )}

      {customBlocks.length > 0 && (
        <>
          <div className="route-note" style={{ marginBottom: 4 }}>
            Or restrict to a hand-drawn area
          </div>
          <select
            className="tile-select"
            value={selectedAreaId ?? ""}
            onChange={(e) => onSelectArea(e.target.value || null)}
          >
            <option value="">(no area filter)</option>
            {customBlocks.map((b) => (
              <option key={b.id} value={b.id}>
                {b.vineyard_id}
              </option>
            ))}
          </select>
        </>
      )}

      <div className="export-buttons" style={{ marginTop: 10 }}>
        <button className="btn btn--primary" onClick={onCompute} disabled={computing}>
          {computing ? (
            <>
              <span className="spinner" /> Computing…
            </>
          ) : (
            "Compute route"
          )}
        </button>
        {isCustomRoute && (
          <button className="btn btn--ghost" onClick={onResetToDefault}>
            Reset to default
          </button>
        )}
      </div>

      {lastResult && (
        <div
          className={lastResult.found ? "route-result route-result--ok" : "route-result route-result--warn"}
        >
          {lastResult.found
            ? "Route computed — see the map and the Route panel below."
            : lastResult.message}
        </div>
      )}
    </div>
  );
}
