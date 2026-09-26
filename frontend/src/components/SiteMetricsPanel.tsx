import type { TileBlockMetrics } from "../types";
import { LAYER_COLORS } from "../colors";

function StatTile({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <div className="stat-tile">
      <div className="stat-value" style={{ color }}>
        {value}
      </div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function SiteMetricsPanel({ blocks }: { blocks: TileBlockMetrics[] | null }) {
  if (!blocks) {
    return (
      <div className="panel stat-grid-loading" aria-busy="true">
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
    );
  }

  if (blocks.length === 0) {
    return <div className="panel route-note">No annotated blocks on this tile.</div>;
  }

  return (
    <>
      {blocks.map((b) => (
        <div key={b.vineyard_id} className="panel">
          <div className="panel-label">Vineyard block</div>
          <div className="panel-title">{b.vineyard_id}</div>
          <div className="stat-grid" style={{ marginTop: 10 }}>
            <StatTile
              value={`${b.canopy_area_m2} m²`}
              label={`canopy area · ${b.canopy_count} plants`}
              color={LAYER_COLORS.canopies.stroke}
            />
            <StatTile
              value={`${b.interrow_area_m2} m²`}
              label="inter-row area"
              color={LAYER_COLORS.interrows.stroke}
            />
            <StatTile
              value={`${b.total_row_length_m} m`}
              label={`total row length · ${b.row_count} rows`}
              color={LAYER_COLORS.rows.stroke}
            />
            <StatTile value={`${b.canopy_count}`} label="canopy count" />
          </div>
        </div>
      ))}
    </>
  );
}
