import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  MapContainer,
  Pane,
  Polygon,
  Polyline,
  Rectangle,
  ImageOverlay,
  CircleMarker,
  Tooltip,
  ScaleControl,
  useMap,
  useMapEvents,
} from "react-leaflet";
import {
  CRS,
  canvas,
  Control,
  DomEvent,
  DomUtil,
  LatLngExpression,
  LatLngBoundsExpression,
} from "leaflet";
import "leaflet/dist/leaflet.css";
import type {
  RouteInfraResponse,
  TileSummary,
  TileLayersResponse,
  SiteLayerKey,
  RealRouteResponse,
  CustomBlock,
} from "../types";
import { LAYER_COLORS } from "../colors";
import { api } from "../api/client";
import { clipLayers, makeArea, pointInArea, type ClipArea } from "../areaClip";

// Everything here is already on ONE local metre plane, real EPSG:32635 minus
// the study area's south-west corner (see backend app/data/real_site.py) —
// so the real tiles, the real passages/forbidden zones and the real start
// point all line up correctly without any reprojection on this side.
// Leaflet's CRS.Simple just needs a consistent Cartesian plane: (x, y) -> (lat, lng) = (y, x).
const toLatLng = ([x, y]: [number, number]): LatLngExpression => [y, x];
const toLatLngRing = (ring: number[][]): LatLngExpression[] =>
  ring.map(([x, y]) => toLatLng([x, y]));
const fromLatLng = (lat: number, lng: number): [number, number] => [lng, lat];

function polygonPositions(geometry: { type: string; coordinates: any }): any {
  if (geometry.type === "Polygon") {
    return geometry.coordinates.map(toLatLngRing);
  }
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.map((poly: number[][][]) => poly.map(toLatLngRing));
  }
  return [];
}

function boundsOverlap(
  a: [number, number, number, number],
  b: [number, number, number, number]
): boolean {
  return a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
}

function FitToBounds({ bounds }: { bounds: [number, number, number, number] }) {
  const map = useMap();
  useEffect(() => {
    const [x0, y0, x1, y1] = bounds;
    map.fitBounds(
      [
        [y0, x0],
        [y1, x1],
      ],
      { padding: [24, 24] }
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bounds.join(","), map]);
  return null;
}

// CRS.Simple: zoom 0 = 1 m/px, which is where the scale bar reads 100 m.
const CENTER_ZOOM = 0;

const CROSSHAIR_SVG =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
  '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>' +
  '<path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>';

/** A native Leaflet button under the +/- zoom control that recenters the
 * map on `target` (the selected tile, or the site) at the 100 m scale. */
function CenterControl({ target }: { target: [number, number] }) {
  const map = useMap();
  // The control is built once; read the latest target at click time.
  const targetRef = useRef(target);
  targetRef.current = target;

  useEffect(() => {
    const control = new Control({ position: "topleft" });
    control.onAdd = () => {
      const bar = DomUtil.create("div", "leaflet-bar leaflet-control");
      const btn = DomUtil.create("a", "center-control", bar) as HTMLAnchorElement;
      btn.href = "#";
      btn.title = "Center map (100 m scale)";
      btn.setAttribute("role", "button");
      btn.setAttribute("aria-label", "Center map at 100 m scale");
      btn.innerHTML = CROSSHAIR_SVG;
      DomEvent.disableClickPropagation(bar);
      DomEvent.on(btn, "click", (e) => {
        DomEvent.preventDefault(e);
        map.setView(toLatLng(targetRef.current), CENTER_ZOOM);
      });
      return bar;
    };
    control.addTo(map);
    return () => {
      control.remove();
    };
  }, [map]);
  return null;
}

/** Tracks the visible map extent (in our local plane) so we only fetch/render
 * tile photos actually in view — 311 full-res JPEGs at once would choke the
 * browser, but a handful in the current viewport is fine. */
function useViewBounds(): [number, number, number, number] | null {
  const [view, setView] = useState<[number, number, number, number] | null>(null);
  const map = useMapEvents({
    moveend: () => {
      const b = map.getBounds();
      setView([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    },
    zoomend: () => {
      const b = map.getBounds();
      setView([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    },
  });
  useEffect(() => {
    const b = map.getBounds();
    setView([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return view;
}

function MapClickCapture({ onClick }: { onClick: (point: [number, number]) => void }) {
  useMapEvents({
    click: (e) => onClick(fromLatLng(e.latlng.lat, e.latlng.lng)),
  });
  return null;
}

const MAX_MOSAIC_TILES = 120;
// Each tile's layers are ~200KB of GeoJSON — cap how many are pulled in at once.
const MAX_LAYER_TILES = 40;

interface Props {
  routeInfra: RouteInfraResponse | null;
  tiles: TileSummary[];
  selectedTile: string | null;
  onSelectTile: (tile: string) => void;
  tileLayers: TileLayersResponse | null;
  visible: Record<SiteLayerKey, boolean>;
  highlightRowId?: string | null;
  focusBounds: [number, number, number, number] | null;
  realRoute: RealRouteResponse | null;
  customBlocks: CustomBlock[];
  /** The route planner's selected area (local ring) — when set, detection
   * layers and targets are only drawn inside it. */
  areaFilter: [number, number][] | null;
  drawMode: boolean;
  draftPoints: [number, number][];
  onDrawClick: (point: [number, number]) => void;
  startMode: boolean;
  customStart: [number, number] | null;
  onStartClick: (point: [number, number]) => void;
}

export function SiteMap({
  routeInfra,
  tiles,
  selectedTile,
  onSelectTile,
  tileLayers,
  visible,
  highlightRowId,
  focusBounds,
  realRoute,
  customBlocks,
  areaFilter,
  drawMode,
  draftPoints,
  onDrawClick,
  startMode,
  customStart,
  onStartClick,
}: Props) {
  const extent = routeInfra?.extent ?? [1000, 1000];
  const pickMode = drawMode || startMode;
  const area = useMemo(() => (areaFilter ? makeArea(areaFilter) : null), [areaFilter]);
  const shownLayers = useMemo(
    () => (tileLayers && area ? clipLayers(tileLayers, area) : tileLayers),
    [tileLayers, area]
  );

  return (
    <MapContainer
      crs={CRS.Simple}
      bounds={[
        [0, 0],
        [extent[1], extent[0]],
      ]}
      style={{
        height: "100%",
        width: "100%",
        background: "#10140f",
        cursor: pickMode ? "crosshair" : undefined,
      }}
      zoomSnap={0.1}
      // CRS.Simple: zoom 0 = 1 m/px, each step down doubles that. Leaflet's
      // default floor is 0 (~100 m scale bar), so allow zooming further out
      // for more context — -2 = 4 m/px, scale bar tops out at 500 m.
      minZoom={-2}
    >
      {focusBounds && <FitToBounds bounds={focusBounds} />}
      <CenterControl
        target={
          focusBounds
            ? [(focusBounds[0] + focusBounds[2]) / 2, (focusBounds[1] + focusBounds[3]) / 2]
            : [extent[0] / 2, extent[1] / 2]
        }
      />
      {drawMode && <MapClickCapture onClick={onDrawClick} />}
      {startMode && <MapClickCapture onClick={onStartClick} />}
      <ScaleControl position="bottomleft" metric imperial={false} maxWidth={140} />
      <TileMosaic
        tiles={tiles}
        selectedTile={selectedTile}
        onSelectTile={onSelectTile}
        interactive={!pickMode}
      />
      <OtherTileLayers
        tiles={tiles}
        selectedTile={selectedTile}
        visible={visible}
        area={area}
      />

      {visible.studyArea &&
        routeInfra?.study_area.features.map((f, i) => (
          <Polygon
            key={i}
            positions={polygonPositions(f.geometry)}
            pathOptions={{
              color: LAYER_COLORS.studyArea.stroke,
              weight: 2.5,
              fill: false,
              dashArray: "6 4",
            }}
          />
        ))}

      {visible.forbidden &&
        routeInfra?.forbidden.features.map((f, i) => (
          <Polygon
            key={i}
            positions={polygonPositions(f.geometry)}
            pathOptions={{
              color: LAYER_COLORS.forbidden.stroke,
              weight: 2,
              fillColor: LAYER_COLORS.forbidden.fill,
              fillOpacity: 0.18,
            }}
          />
        ))}

      {visible.passages &&
        routeInfra?.passages.features.map((f, i) => (
          <Polygon
            key={i}
            positions={polygonPositions(f.geometry)}
            pathOptions={{
              color: LAYER_COLORS.passages.stroke,
              weight: 2,
              fillColor: LAYER_COLORS.passages.fill,
              fillOpacity: 0.12,
            }}
          />
        ))}

      {customStart && (
        <CircleMarker
          center={toLatLng(customStart)}
          radius={8}
          pathOptions={{ color: "#1a202c", fillColor: "#1baf7a", fillOpacity: 1, weight: 2 }}
        >
          <Tooltip permanent direction="top" offset={[0, -8]}>
            START
          </Tooltip>
        </CircleMarker>
      )}

      {visible.interrows &&
        shownLayers?.interrows.features.map((f, i) => (
          <Polygon
            key={i}
            positions={polygonPositions(f.geometry)}
            pathOptions={{ color: LAYER_COLORS.interrows.stroke, weight: 2, fillOpacity: 0.15 }}
          />
        ))}

      {visible.canopies &&
        shownLayers?.canopies.features.map((f, i) => {
          const isHighlighted = highlightRowId && f.properties.row_id === highlightRowId;
          return (
            <Polygon
              key={i}
              positions={polygonPositions(f.geometry)}
              pathOptions={{
                color: LAYER_COLORS.canopies.stroke,
                weight: 2,
                fillColor: LAYER_COLORS.canopies.fill,
                fillOpacity: isHighlighted ? 0.95 : 0.65,
              }}
            />
          );
        })}

      {visible.rows &&
        shownLayers?.rows.features.map((f, i) => {
          const isHighlighted = highlightRowId && f.properties.row_id === highlightRowId;
          return (
            <Polyline
              key={i}
              positions={f.geometry.coordinates.map((c: [number, number]) => toLatLng(c))}
              pathOptions={{
                color: isHighlighted ? "#1a202c" : LAYER_COLORS.rows.stroke,
                weight: isHighlighted ? 5 : 3,
              }}
            >
              <Tooltip>{f.properties.row_id}</Tooltip>
            </Polyline>
          );
        })}

      {visible.waste &&
        shownLayers?.waste.features.map((f, i) => (
          <Polygon
            key={i}
            positions={polygonPositions(f.geometry)}
            pathOptions={{ color: LAYER_COLORS.waste.stroke, weight: 3, fillOpacity: 0.3 }}
          />
        ))}

      {visible.route && realRoute?.route && (
        <Polyline
          positions={realRoute.route.polyline_local.map((c) => toLatLng(c as [number, number]))}
          pathOptions={{ color: LAYER_COLORS.route.stroke, weight: 5 }}
        />
      )}

      {visible.targets &&
        realRoute?.targets
          .filter((t) => !area || pointInArea(t.point, area))
          .map((t) => (
            <CircleMarker
              key={t.id}
              center={toLatLng(t.point)}
              radius={6}
              pathOptions={{
                color: t.kind === "waste" ? LAYER_COLORS.waste.stroke : LAYER_COLORS.targets.stroke,
                fillColor:
                  t.kind === "waste" ? LAYER_COLORS.waste.stroke : LAYER_COLORS.targets.stroke,
                fillOpacity: 1,
                weight: 2,
              }}
            >
              <Tooltip>{t.id}</Tooltip>
            </CircleMarker>
          ))}

      {visible.customBlocks &&
        customBlocks.map((b) => (
          <Polygon
            key={b.id}
            positions={toLatLngRing(b.polygon)}
            pathOptions={{
              color: LAYER_COLORS.customBlock.stroke,
              weight: 3,
              dashArray: "5 3",
              fillOpacity: 0.08,
            }}
          >
            <Tooltip>{b.vineyard_id}</Tooltip>
          </Polygon>
        ))}

      {drawMode && draftPoints.length > 0 && (
        <>
          <Polyline
            positions={draftPoints.map(toLatLng)}
            pathOptions={{ color: LAYER_COLORS.draft.stroke, weight: 3, dashArray: "4 4" }}
          />
          {draftPoints.map((p, i) => (
            <CircleMarker
              key={i}
              center={toLatLng(p)}
              radius={5}
              pathOptions={{
                color: LAYER_COLORS.draft.stroke,
                fillColor: "#fff",
                fillOpacity: 1,
                weight: 2,
              }}
            />
          ))}
        </>
      )}
    </MapContainer>
  );
}

/** The real-photo mosaic: only tiles intersecting the current viewport are
 * fetched/rendered, so panning the full 311-tile site stays smooth instead
 * of loading every tile's JPEG up front. */
function TileMosaic({
  tiles,
  selectedTile,
  onSelectTile,
  interactive,
}: {
  tiles: TileSummary[];
  selectedTile: string | null;
  onSelectTile: (tile: string) => void;
  interactive: boolean;
}) {
  const viewBounds = useViewBounds();

  const visibleTiles = useMemo(() => {
    if (!viewBounds) return [];
    const matches = tiles.filter((t) => boundsOverlap(t.bounds, viewBounds));
    return matches.slice(0, MAX_MOSAIC_TILES);
  }, [tiles, viewBounds]);

  // While drawing a block boundary, the mosaic must not intercept clicks —
  // otherwise every click lands on a tile's photo/footprint (which covers
  // the whole viewport) and never reaches the map's own click handler that
  // the draw tool listens on, so points never accumulate. `interactive`
  // toggles Leaflet's own hit-testing for these layers off during drawing.
  return (
    <>
      {visibleTiles.map((t) => {
        const [x0, y0, x1, y1] = t.bounds;
        const bounds: LatLngBoundsExpression = [
          [y0, x0],
          [y1, x1],
        ];
        return (
          <ImageOverlay
            key={t.tile}
            url={api.tileImageUrl(t.tile)}
            bounds={bounds}
            interactive={interactive}
            eventHandlers={interactive ? { click: () => onSelectTile(t.tile) } : {}}
          />
        );
      })}
      {/* Footprint outlines: only ground-truth tiles (rare, worth flagging as
          verified) plus whichever tile is selected — the other ~230
          classical-CV tiles would just be visual noise at this density. */}
      {visibleTiles
        .filter((t) => t.source === "ground_truth" || t.tile === selectedTile)
        .map((t) => {
          const [x0, y0, x1, y1] = t.bounds;
          const isSelected = t.tile === selectedTile;
          return (
            <Rectangle
              key={`fp-${t.tile}`}
              bounds={[
                [y0, x0],
                [y1, x1],
              ]}
              pathOptions={{
                color: isSelected ? "#fff" : LAYER_COLORS.tileFootprint.stroke,
                weight: isSelected ? 2 : 1.5,
                fill: false,
                dashArray: isSelected ? undefined : "3 5",
              }}
              interactive={interactive}
              eventHandlers={interactive ? { click: () => onSelectTile(t.tile) } : {}}
            >
              <Tooltip>
                {t.tile}
                {t.source === "ground_truth" ? " — ground truth" : ""}
                {t.source === "classical_cv" ? " — auto CV (unverified)" : ""}
              </Tooltip>
            </Rectangle>
          );
        })}
    </>
  );
}

/** Canopies / inter-rows / rows / waste of every annotated tile in view, not
 * just the selected one (which the main SiteMap body draws, with row
 * highlighting). Fetched lazily per tile as it comes into view and cached.
 * Drawn on a canvas in a pointer-events:none pane: a few dozen tiles means
 * thousands of polygons (too many for SVG), and the canvas must not swallow
 * clicks meant for the tile photos underneath (tile selection). */
function OtherTileLayers({
  tiles,
  selectedTile,
  visible,
  area,
}: {
  tiles: TileSummary[];
  selectedTile: string | null;
  visible: Record<SiteLayerKey, boolean>;
  area: ClipArea | null;
}) {
  const viewBounds = useViewBounds();
  const renderer = useMemo(() => canvas({ pane: "otherTileLayers", padding: 0.5 }), []);
  const [layersByTile, setLayersByTile] = useState<Record<string, TileLayersResponse>>({});
  const requested = useRef(new Set<string>());
  const generation = useRef(0);

  // A new tile list (after an upload, possibly overwriting a tile) may carry
  // new detections — drop the cache so tiles in view are refetched.
  useEffect(() => {
    generation.current += 1;
    requested.current = new Set();
    setLayersByTile({});
  }, [tiles]);

  const inView = useMemo(() => {
    if (!viewBounds) return [];
    const cx = (viewBounds[0] + viewBounds[2]) / 2;
    const cy = (viewBounds[1] + viewBounds[3]) / 2;
    const dist = (t: TileSummary) =>
      Math.hypot((t.bounds[0] + t.bounds[2]) / 2 - cx, (t.bounds[1] + t.bounds[3]) / 2 - cy);
    return tiles
      .filter((t) => t.annotated && t.tile !== selectedTile && boundsOverlap(t.bounds, viewBounds))
      .sort((a, b) => dist(a) - dist(b))
      .slice(0, MAX_LAYER_TILES);
  }, [tiles, viewBounds, selectedTile]);

  useEffect(() => {
    const gen = generation.current;
    for (const t of inView) {
      if (requested.current.has(t.tile)) continue;
      requested.current.add(t.tile);
      api
        .getTileLayers(t.tile)
        .then((layers) => {
          if (gen === generation.current) setLayersByTile((m) => ({ ...m, [t.tile]: layers }));
        })
        .catch((e) => {
          requested.current.delete(t.tile);
          console.error(`Layers for ${t.tile} unavailable:`, e);
        });
    }
  }, [inView]);

  const shownByTile = useMemo(() => {
    if (!area) return layersByTile;
    const out: Record<string, TileLayersResponse> = {};
    for (const [name, layers] of Object.entries(layersByTile)) out[name] = clipLayers(layers, area);
    return out;
  }, [layersByTile, area]);

  const common = { renderer, interactive: false };

  return (
    <Pane name="otherTileLayers" style={{ zIndex: 401, pointerEvents: "none" }}>
      {inView.map((t) => {
        const layers = shownByTile[t.tile];
        if (!layers) return null;
        return (
          <Fragment key={t.tile}>
            {visible.interrows &&
              layers.interrows.features.map((f, i) => (
                <Polygon
                  key={`ir${i}`}
                  {...common}
                  positions={polygonPositions(f.geometry)}
                  pathOptions={{ color: LAYER_COLORS.interrows.stroke, weight: 2, fillOpacity: 0.15 }}
                />
              ))}
            {visible.canopies &&
              layers.canopies.features.map((f, i) => (
                <Polygon
                  key={`c${i}`}
                  {...common}
                  positions={polygonPositions(f.geometry)}
                  pathOptions={{
                    color: LAYER_COLORS.canopies.stroke,
                    weight: 2,
                    fillColor: LAYER_COLORS.canopies.fill,
                    fillOpacity: 0.65,
                  }}
                />
              ))}
            {visible.rows &&
              layers.rows.features.map((f, i) => (
                <Polyline
                  key={`r${i}`}
                  {...common}
                  positions={f.geometry.coordinates.map((c: [number, number]) => toLatLng(c))}
                  pathOptions={{ color: LAYER_COLORS.rows.stroke, weight: 3 }}
                />
              ))}
            {visible.waste &&
              layers.waste.features.map((f, i) => (
                <Polygon
                  key={`w${i}`}
                  {...common}
                  positions={polygonPositions(f.geometry)}
                  pathOptions={{ color: LAYER_COLORS.waste.stroke, weight: 3, fillOpacity: 0.3 }}
                />
              ))}
          </Fragment>
        );
      })}
    </Pane>
  );
}
