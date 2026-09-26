import { useState } from "react";
import type { CustomBlock } from "../types";

interface Props {
  drawMode: boolean;
  pointCount: number;
  onStart: () => void;
  onUndo: () => void;
  onCancel: () => void;
  onSave: (vineyardId: string) => Promise<void>;
  customBlocks: CustomBlock[];
  onDelete: (id: string) => void;
}

export function DrawBlockPanel({
  drawMode,
  pointCount,
  onStart,
  onUndo,
  onCancel,
  onSave,
  customBlocks,
  onDelete,
}: Props) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const canFinish = pointCount >= 3;

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await onSave(name.trim());
      setName("");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel">
      <h3>Set vineyard field area</h3>
      {!drawMode ? (
        <>
          <div className="route-note" style={{ marginBottom: 8 }}>
            Trace a block's boundary by hand — click points on the map, close
            the loop, save it under a vineyard_id.
          </div>
          <button className="btn btn--primary" onClick={onStart}>
            Draw vineyard area
          </button>
        </>
      ) : (
        <>
          <div className="route-note" style={{ marginBottom: 8 }}>
            Click the map to add points ({pointCount} so far). Need at least 3.
          </div>
          {canFinish && (
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              <input
                className="tile-select"
                style={{ margin: 0 }}
                placeholder="vineyard_id, e.g. V03"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <button
                className="btn btn--primary"
                style={{ flex: "0 0 auto" }}
                disabled={!name.trim() || saving}
                onClick={handleSave}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          )}
          <div className="export-buttons">
            <button className="btn btn--ghost" onClick={onUndo} disabled={pointCount === 0}>
              Undo point
            </button>
            <button className="btn btn--ghost" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </>
      )}

      {customBlocks.length > 0 && (
        <ul className="custom-block-list">
          {customBlocks.map((b) => (
            <li key={b.id}>
              <span>{b.vineyard_id}</span>
              <button
                className="custom-block-delete"
                title="Delete this area"
                onClick={() => onDelete(b.id)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
