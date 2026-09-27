import type { TileBlockMetrics, RealRoute } from "../types";

function Cell({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat-bar-cell">
      <div className="stat-bar-value">{value}</div>
      <div className="stat-bar-label">{label}</div>
    </div>
  );
}

export function StatBar({
  blocks,
  route,
}: {
  blocks: TileBlockMetrics[] | null;
  route: RealRoute | null;
}) {
  if (!blocks) return null;

  const rowCount = blocks.reduce((sum, b) => sum + b.row_count, 0);
  const canopyHa = blocks.reduce((sum, b) => sum + b.canopy_area_ha, 0);
  const interrowHa = blocks.reduce((sum, b) => sum + b.interrow_area_ha, 0);

  return (
    <div className="stat-bar">
      <Cell value={String(blocks.length)} label="blocks" />
      <Cell value={String(rowCount)} label="rows" />
      <Cell value={`${canopyHa.toFixed(2)} ha`} label="vineyard" />
      <Cell value={`${interrowHa.toFixed(2)} ha`} label="inter-row" />
      {route ? (
        <Cell value={`${(route.length_m / 1000).toFixed(1)} km`} label="route" />
      ) : (
        <Cell value="—" label="route" />
      )}
    </div>
  );
}
