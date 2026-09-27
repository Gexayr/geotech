import { useRef, useState } from "react";
import { api } from "../api/client";

const MAX_FILES = 5;

interface UploadResult {
  filename: string;
  ok: boolean;
  error?: string;
  canopies?: number;
  rows?: number;
}

export function UploadPanel({ onUploaded }: { onUploaded: (uploadedFilenames: string[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [xmlUrl, setXmlUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    if (files.length > MAX_FILES) {
      setError(`Max ${MAX_FILES} files at once (picked ${files.length})`);
      return;
    }
    setError(null);
    setUploading(true);
    setResults(null);
    setXmlUrl(null);
    try {
      const res = await api.uploadTiles(files);
      setResults(res.results);
      setXmlUrl(res.annotation_xml_url);
      const okNames = res.results.filter((r) => r.ok).map((r) => r.filename);
      if (okNames.length > 0) onUploaded(okNames);
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div className="panel">
      <h3>Upload your field</h3>
      <div className="route-note" style={{ marginBottom: 8 }}>
        Up to {MAX_FILES} georeferenced .tif/.tiff tiles — detected on the spot with the
        same classical-CV fallback used for the rest of the site (no trained model).
        Route planning isn't available for uploaded fields (no passages/forbidden data).
      </div>

      <input
        ref={inputRef}
        type="file"
        accept=".tif,.tiff"
        multiple
        onChange={(e) => handleFiles(e.target.files)}
        disabled={uploading}
        style={{ fontSize: 12, color: "var(--text-secondary)" }}
      />

      {uploading && (
        <div className="route-note--strong" style={{ marginTop: 8 }}>
          <span className="spinner" /> Uploading & detecting…
        </div>
      )}
      {error && <div className="route-result route-result--warn">{error}</div>}

      {results && (
        <ul className="custom-block-list" style={{ marginTop: 10 }}>
          {results.map((r) => (
            <li key={r.filename}>
              <span>{r.filename}</span>
              <span style={{ color: r.ok ? "var(--ok-text)" : "var(--danger)", fontSize: 12 }}>
                {r.ok ? `${r.canopies} canopies, ${r.rows} rows` : r.error}
              </span>
            </li>
          ))}
        </ul>
      )}

      {xmlUrl && (
        <a
          className="btn btn--ghost"
          style={{ display: "block", textAlign: "center", textDecoration: "none", marginTop: 8 }}
          href={api.apiUrl(xmlUrl)}
          download
        >
          Download this upload's CVAT annotations.xml
        </a>
      )}
    </div>
  );
}
