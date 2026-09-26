import type { TileSummary } from "../types";

interface Props {
  tiles: TileSummary[];
  selected: string | null;
  onSelect: (tile: string) => void;
}

export function TileSelector({ tiles, selected, onSelect }: Props) {
  return (
    <div className="panel">
      <div className="panel-label">Selected tile</div>
      <select
        className="tile-select"
        value={selected ?? ""}
        onChange={(e) => onSelect(e.target.value)}
      >
        {tiles.map((t) => (
          <option key={t.tile} value={t.tile}>
            {t.tile}
            {t.vineyard_ids.length > 0 ? ` — ${t.vineyard_ids.join(", ")}` : ""}
            {t.source === "ground_truth" ? " (ground truth)" : ""}
            {t.source === "classical_cv" ? " (auto CV)" : ""}
            {t.source === "uploaded" ? " (your upload)" : ""}
          </option>
        ))}
      </select>
      <div className="route-note">
        {tiles.length} tiles in the catalog — click a dashed rectangle on the map to
        switch.
      </div>
    </div>
  );
}
