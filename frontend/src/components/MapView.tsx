import { MapContainer, Polygon, Polyline, CircleMarker, Tooltip, ScaleControl } from "react-leaflet";
import { CRS, LatLngExpression, LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import type { LayersResponse, LayerKey, RouteResponse } from "../types";
import { LAYER_COLORS } from "../colors";

// Demo geometry lives on a local metre grid (x = east, y = north), not real
// GPS. Leaflet's CRS.Simple treats the plane as unprojected, so we just map
// (x, y) -> (lat, lng) = (y, x). Swap for a real georeferenced CRS + tile
// layer once actual drone orthomosaics are wired in.
const toLatLng = ([x, y]: [number, number]): LatLngExpression => [y, x];

const ringToLatLngs = (ring: [number, number][]): LatLngExpression[] => ring.map(toLatLng);

interface Props {
  layers: LayersResponse | null;
  route: RouteResponse | null;
  visible: Record<LayerKey, boolean>;
  highlightRowId?: string | null;
}

const BOUNDS: LatLngBoundsExpression = [
  [-8, -2],
  [38, 20],
];

export function MapView({ layers, route, visible, highlightRowId }: Props) {
  return (
    <MapContainer
      crs={CRS.Simple}
      bounds={BOUNDS}
      style={{ height: "100%", width: "100%", background: "#eef2ea" }}
      zoomSnap={0.25}
    >
      <ScaleControl position="bottomleft" metric imperial={false} maxWidth={120} />

      {visible.interrows &&
        layers?.interrows.features.map((f) => (
          <Polygon
            key={f.properties.interrow_id}
            positions={ringToLatLngs(f.geometry.coordinates[0])}
            pathOptions={{ color: LAYER_COLORS.interrows.stroke, weight: 1, fillOpacity: 0.15 }}
          />
        ))}

      {visible.canopies &&
        layers?.canopies.features.map((f) => {
          const isHighlighted = highlightRowId && f.properties.row_id === highlightRowId;
          return (
            <Polygon
              key={f.properties.canopy_id}
              positions={ringToLatLngs(f.geometry.coordinates[0])}
              pathOptions={{
                color: isHighlighted ? "#1a202c" : LAYER_COLORS.canopies.stroke,
                weight: isHighlighted ? 2 : 1,
                fillColor: LAYER_COLORS.canopies.fill,
                fillOpacity: isHighlighted ? 0.95 : 0.7,
              }}
            >
              <Tooltip>{f.properties.canopy_id}</Tooltip>
            </Polygon>
          );
        })}

      {visible.rows &&
        layers?.rows.features.map((f) => {
          const isHighlighted = highlightRowId && f.properties.row_id === highlightRowId;
          return (
            <Polyline
              key={f.properties.row_id}
              positions={f.geometry.coordinates.map(toLatLng)}
              pathOptions={{
                color: isHighlighted ? "#1a202c" : LAYER_COLORS.rows.stroke,
                weight: isHighlighted ? 4 : 2,
                dashArray: isHighlighted ? undefined : "4 4",
              }}
            >
              <Tooltip>{f.properties.row_id}</Tooltip>
            </Polyline>
          );
        })}

      {visible.waste &&
        layers?.waste.features.map((f) => (
          <Polygon
            key={f.properties.waste_id}
            positions={ringToLatLngs(f.geometry.coordinates[0])}
            pathOptions={{ color: LAYER_COLORS.waste.stroke, weight: 2, fillOpacity: 0.3 }}
          >
            <Tooltip>{f.properties.waste_id}</Tooltip>
          </Polygon>
        ))}

      {visible.targets &&
        layers?.targets.features.map((f) => {
          const isStart = f.properties.kind === "start_end";
          const [x, y] = f.geometry.coordinates as [number, number];
          return (
            <CircleMarker
              key={f.properties.target_id}
              center={toLatLng([x, y])}
              radius={isStart ? 7 : 6}
              pathOptions={{
                color: isStart ? "#1a202c" : LAYER_COLORS.targets.stroke,
                fillColor: isStart ? "#fff" : LAYER_COLORS.targets.stroke,
                fillOpacity: 1,
                weight: 2,
              }}
            >
              <Tooltip permanent direction="top" offset={[0, -8]}>
                {f.properties.target_id}
              </Tooltip>
            </CircleMarker>
          );
        })}

      {visible.route && route && (
        <Polyline
          positions={route.polyline.map(([x, y]) => toLatLng([x, y]))}
          pathOptions={{ color: LAYER_COLORS.route.stroke, weight: 3 }}
        />
      )}
    </MapContainer>
  );
}
