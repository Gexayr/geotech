import type { TileSummary } from "../types";

interface Props {
  tiles: TileSummary[];
  selected: string | null;
  onSelect: (tile: string) => void;
  onDelete: (tile: string) => void;
  deleting: boolean;
}

export function TileSelector({ tiles, selected, onSelect, onDelete, deleting }: Props) {
  return (
    <div className="panel">
      <div className="panel-label">Selected tile</div>
      <div className="tile-select-row">
        <select
          className="tile-select"
          value={selected ?? ""}
          disabled={deleting}
          onChange={(e) => onSelect(e.target.value)}
        >
          {tiles.map((t) => (
            <option key={t.tile} value={t.tile}>
              {t.tile}
              {t.vineyard_ids.length > 0 ? ` — ${t.vineyard_ids.join(", ")}` : ""}
              {t.source === "ground_truth" ? " (ground truth)" : ""}
              {t.source === "classical_cv" ? " (auto CV)" : ""}
              {t.source === "uploaded" ? " (your upload)" : ""}
              {t.source === "model" ? " (model)" : ""}
            </option>
          ))}
        </select>
        <button
          className="custom-block-delete tile-delete"
          title="Delete this tile"
          aria-label={deleting ? "Deleting tile" : "Delete this tile"}
          disabled={!selected || deleting}
          onClick={() => selected && onDelete(selected)}
        >
          {deleting ? <span className="spinner" /> : "×"}
        </button>
      </div>
      <div className="route-note">
        {tiles.length} tiles in the catalog — click a dashed rectangle on the map to
        switch.
      </div>
    </div>
  );
}
