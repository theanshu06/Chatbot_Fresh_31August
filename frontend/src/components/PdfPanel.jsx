import { useState } from "react";
import { ingestPdf } from "../api.js";

export default function PdfPanel({ onIngested }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const data = await ingestPdf(file);
      setResult(data);
      onIngested?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <h2>PDF</h2>
      <p className="hint">Upload a PDF — its text is extracted page by page, chunked, and embedded.</p>
      <input type="file" accept=".pdf" onChange={(e) => setFile(e.target.files[0] || null)} />
      <button type="submit" disabled={!file || busy}>
        {busy ? "Ingesting…" : "Ingest PDF"}
      </button>
      {error && <div className="error">{error}</div>}
      {result && (
        <div className="result">
          <div><strong>{result.filename}</strong></div>
          <div>chunks stored: {result.chunks}</div>
          <div className="muted">source_id: {result.source_id}</div>
        </div>
      )}
    </form>
  );
}
