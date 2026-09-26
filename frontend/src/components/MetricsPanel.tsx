import type { MetricsResponse, RouteResponse } from "../types";
import { LAYER_COLORS } from "../colors";

interface Props {
  metrics: MetricsResponse | null;
  route: RouteResponse | null;
}

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

export function MetricsPanel({ metrics, route }: Props) {
  if (!metrics || !route) {
    return (
      <div className="panel stat-grid-loading" aria-busy="true">
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
    );
  }

  return (
    <div className="stat-grid">
      <StatTile value={`${route.length_m} m`} label="route length" color={LAYER_COLORS.route.stroke} />
      <StatTile value={`${route.targets_visited} / ${route.targets_total}`} label="targets visited" />
      <StatTile
        value={`${metrics.canopy_area_m2} m²`}
        label="canopy area"
        color={LAYER_COLORS.canopies.stroke}
      />
      <StatTile
        value={`${metrics.interrow_area_m2} m²`}
        label="inter-row area"
        color={LAYER_COLORS.interrows.stroke}
      />
    </div>
  );
}
