// Central color tokens shared by the map, the layer legend and the route
// chart, so the same entity always reads the same color across the app.
// The "route" hue is pulled from the validated dataviz palette (categorical
// slot 8, red) since it's the one color used in an actual chart (the route
// comparison dumbbell); the rest are fixed semantic map colors mirroring the
// brief's own mockup (green canopy, cyan rows, orange waste, purple targets).

export const LAYER_COLORS = {
  canopies: { stroke: "#2f855a", fill: "#68a678" },
  rows: { stroke: "#0891b2" },
  interrows: { stroke: "#38b2ac" },
  waste: { stroke: "#c05621" },
  targets: { stroke: "#805ad5" },
  auditTargets: { stroke: "#d69e2e" }, // auditor verification points (model service)
  route: { stroke: "#e34948", light: "#f4c3c2" }, // dataviz palette slot 8 (red), + a light tint for "before"
  studyArea: { stroke: "#c3c2b7" },
  passages: { stroke: "#1baf7a", fill: "#1baf7a" },
  forbidden: { stroke: "#e34948", fill: "#e34948" },
  tileFootprint: { stroke: "#2a78d6" },
  customBlock: { stroke: "#2a78d6", fill: "#2a78d6" },
  draft: { stroke: "#eda100" },
} as const;
