import type { SiteLayerKey } from "../types";
import { LAYER_COLORS } from "../colors";

// The per-tile detail layers (canopies/rows/interrows/waste/targets/route)
// live in the LayerChipBar over the map now — this panel only keeps the
// site-wide and manually-drawn layers, which don't fit as quick chips.
const GROUPS: { title: string; keys: SiteLayerKey[] }[] = [
  { title: "Site", keys: ["studyArea", "passages", "forbidden"] },
  { title: "Manual", keys: ["customBlocks"] },
];

const LABELS: Record<SiteLayerKey, string> = {
  studyArea: "Study area boundary",
  passages: "Authorised passages",
  forbidden: "Forbidden zones",
  canopies: "Vineyard polygons",
  rows: "Row axes",
  interrows: "Inter-row areas",
  waste: "Waste objects",
  targets: "Inspection / waste targets",
  route: "Walking route",
  customBlocks: "Hand-drawn block boundaries",
};

const SWATCH_COLOR: Record<SiteLayerKey, string> = {
  studyArea: LAYER_COLORS.studyArea.stroke,
  passages: LAYER_COLORS.passages.stroke,
  forbidden: LAYER_COLORS.forbidden.stroke,
  canopies: LAYER_COLORS.canopies.stroke,
  rows: LAYER_COLORS.rows.stroke,
  interrows: LAYER_COLORS.interrows.stroke,
  waste: LAYER_COLORS.waste.stroke,
  targets: LAYER_COLORS.targets.stroke,
  route: LAYER_COLORS.route.stroke,
  customBlocks: LAYER_COLORS.customBlock.stroke,
};

interface Props {
  visible: Record<SiteLayerKey, boolean>;
  onToggle: (key: SiteLayerKey) => void;
}

export function SiteLayerPanel({ visible, onToggle }: Props) {
  return (
    <div className="panel">
      <h3>Layers</h3>
      {GROUPS.map((group) => (
        <div key={group.title} className="layer-group">
          <div className="layer-group-title">{group.title}</div>
          <ul className="layer-list">
            {group.keys.map((key) => (
              <li key={key}>
                <label>
                  <input type="checkbox" checked={visible[key]} onChange={() => onToggle(key)} />
                  <i className="swatch" style={{ background: SWATCH_COLOR[key] }} />
                  <span>{LABELS[key]}</span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
