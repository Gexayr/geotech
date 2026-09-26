import type { RouteComparison } from "../types";
import { LAYER_COLORS } from "../colors";

// "Before -> after per item" is a dumbbell, one hue in two shades — not a
// two-bar chart (those are unrelated categories; this is one measure, two
// states of the same trip). Light dot = before (separate return trips),
// full-hue dot = after (the optimized tour) — see dataviz skill,
// choosing-a-form.md.
export function RouteComparisonChart({ comparison }: { comparison: RouteComparison }) {
  const { separate_trips_m, optimized_tour_m, saved_m, reduction_pct } = comparison;
  const max = Math.max(separate_trips_m, optimized_tour_m, 1);
  const beforePct = (separate_trips_m / max) * 100;
  const afterPct = (optimized_tour_m / max) * 100;

  return (
    <div className="panel route-chart">
      <div className="route-chart-header">
        <h3 style={{ margin: 0 }}>Route comparison</h3>
        <span className="route-chart-delta">
          −{saved_m} m · −{reduction_pct}%
        </span>
      </div>

      <div className="dumbbell-track">
        <div className="dumbbell-baseline" />
        <div
          className="dumbbell-connector"
          style={{ left: `${afterPct}%`, width: `${beforePct - afterPct}%` }}
        />
        <div className="dumbbell-dot dumbbell-dot--before" style={{ left: `${beforePct}%` }} />
        <div className="dumbbell-dot dumbbell-dot--after" style={{ left: `${afterPct}%` }} />
      </div>
      <div className="dumbbell-scale">
        <span>0 m</span>
        <span>{max} m</span>
      </div>

      <ul className="dumbbell-legend">
        <li>
          <i className="dot" style={{ background: LAYER_COLORS.route.light }} />
          Separate return trips — <strong>{separate_trips_m} m</strong>
        </li>
        <li>
          <i className="dot" style={{ background: LAYER_COLORS.route.stroke }} />
          Shortest closed tour — <strong>{optimized_tour_m} m</strong>
        </li>
      </ul>
    </div>
  );
}
