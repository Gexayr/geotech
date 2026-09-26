import type { RouteResponse } from "../types";

export function RouteInfo({ route }: { route: RouteResponse | null }) {
  if (!route) return null;

  const path = ["START", ...route.order, "START"].join(" → ");
  const [x, y] = route.start_point;

  return (
    <div className="panel">
      <h3>Route</h3>
      <div className="route-path">{path}</div>
      <div className="route-note">
        Start: ({x}, {y}) in demo metres
      </div>
      <div className="route-note route-note--strong">Route returns to its start.</div>
    </div>
  );
}
