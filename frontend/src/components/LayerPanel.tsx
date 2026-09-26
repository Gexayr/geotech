import type { LayerKey } from "../types";
import { LAYER_COLORS } from "../colors";

const LABELS: Record<LayerKey, string> = {
  canopies: "Canopy polygons",
  rows: "Row axes",
  interrows: "Inter-row passages",
  waste: "Waste objects",
  targets: "Inspection targets",
  route: "Walking route",
};

const SWATCH_COLOR: Record<LayerKey, string> = {
  canopies: LAYER_COLORS.canopies.stroke,
  rows: LAYER_COLORS.rows.stroke,
  interrows: LAYER_COLORS.interrows.stroke,
  waste: LAYER_COLORS.waste.stroke,
  targets: LAYER_COLORS.targets.stroke,
  route: LAYER_COLORS.route.stroke,
};

interface Props {
  visible: Record<LayerKey, boolean>;
  onToggle: (key: LayerKey) => void;
}

export function LayerPanel({ visible, onToggle }: Props) {
  return (
    <div className="panel">
      <h3>Layers</h3>
      <ul className="layer-list">
        {(Object.keys(LABELS) as LayerKey[]).map((key) => (
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
  );
}
