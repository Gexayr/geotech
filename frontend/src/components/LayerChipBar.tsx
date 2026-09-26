import type { SiteLayerKey } from "../types";
import { LAYER_COLORS } from "../colors";

const CHIP_KEYS: SiteLayerKey[] = ["canopies", "rows", "interrows", "waste", "targets", "route"];

const LABELS: Record<string, string> = {
  canopies: "Canopies",
  rows: "Rows",
  interrows: "Inter-row",
  waste: "Waste",
  targets: "Targets",
  route: "Route",
};

const CHIP_COLOR: Record<string, string> = {
  canopies: LAYER_COLORS.canopies.stroke,
  rows: LAYER_COLORS.rows.stroke,
  interrows: LAYER_COLORS.interrows.stroke,
  waste: LAYER_COLORS.waste.stroke,
  targets: LAYER_COLORS.targets.stroke,
  route: LAYER_COLORS.route.stroke,
};

interface Props {
  visible: Record<SiteLayerKey, boolean>;
  onToggle: (key: SiteLayerKey) => void;
}

export function LayerChipBar({ visible, onToggle }: Props) {
  return (
    <div className="chip-bar">
      {CHIP_KEYS.map((key) => (
        <button
          key={key}
          className={visible[key] ? "chip active" : "chip"}
          style={{ background: CHIP_COLOR[key] }}
          onClick={() => onToggle(key)}
        >
          {LABELS[key]}
        </button>
      ))}
    </div>
  );
}
