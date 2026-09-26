import { useState } from "react";

interface Props {
  title: string;
  filename: string;
  data: unknown;
  onClose: () => void;
}

export function ExportModal({ title, filename, data, onClose }: Props) {
  const [copied, setCopied] = useState(false);
  const json = JSON.stringify(data, null, 2);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(json);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard API unavailable (e.g. insecure context) — silently ignore,
      // the download button still works.
    }
  };

  const download = () => {
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <pre className="modal-body">{json}</pre>
        <div className="modal-actions">
          <button className="btn btn--ghost" onClick={copy}>
            {copied ? "Copied" : "Copy JSON"}
          </button>
          <button className="btn btn--primary" onClick={download}>
            Download {filename}
          </button>
        </div>
      </div>
    </div>
  );
}
