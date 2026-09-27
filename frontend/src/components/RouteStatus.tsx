import type { RouteInfraResponse, RealRouteResponse } from "../types";
import { RouteComparisonChart } from "./RouteComparisonChart";

export function RouteStatus({
  routeInfra,
  realRoute,
}: {
  routeInfra: RouteInfraResponse | null;
  realRoute: RealRouteResponse | null;
}) {
  if (!routeInfra) return null;
  const start = routeInfra.start.features[0];

  return (
    <>
      <div className="panel">
        <h3>Route</h3>
        <div className="route-note">
          Real start point: {start.properties.lat}, {start.properties.lon} —{" "}
          {start.properties.description}
        </div>

        {realRoute?.route ? (
          <>
            <div className="route-path" style={{ marginTop: 8 }}>
              START → {realRoute.route.order.join(" → ")} → START
            </div>
            <div className="route-note--strong">Route returns to its start.</div>
          </>
        ) : (
          <div className="route-note--strong" style={{ marginTop: 8 }}>
            {realRoute?.message ?? "Loading…"}
          </div>
        )}
      </div>

      {realRoute?.route && <RouteComparisonChart comparison={realRoute.route.comparison} />}

      {realRoute && realRoute.targets.length > 0 && (
        <details className="panel targets-details">
          <summary>Targets ({realRoute.targets.length})</summary>
          <div className="targets-scroll">
            <table className="row-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Kind</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {realRoute.targets.map((t) => (
                  <tr key={t.id} title={t.reason}>
                    <td>{t.id}</td>
                    <td>{t.kind}</td>
                    <td>{t.row_id ?? t.vineyard_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </>
  );
}
