import { useState } from "react";
import { ingestWebsite } from "../api.js";

export default function WebsitePanel({ onIngested }) {
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(20);
  const [maxDepth, setMaxDepth] = useState(2);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!url) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const data = await ingestWebsite(url, Number(maxPages), Number(maxDepth));
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
      <h2>Website</h2>
      <p className="hint">Crawls same-domain links from the URL you give it, up to the limits below.</p>
      <input type="url" placeholder="https://example.com" value={url} onChange={(e) => setUrl(e.target.value)} required />
      <div className="row">
        <label>
          Max pages
          <input type="number" min={1} max={100} value={maxPages} onChange={(e) => setMaxPages(e.target.value)} />
        </label>
        <label>
          Max depth
          <input type="number" min={0} max={5} value={maxDepth} onChange={(e) => setMaxDepth(e.target.value)} />
        </label>
      </div>
      <button type="submit" disabled={!url || busy}>
        {busy ? "Crawling…" : "Crawl & Ingest"}
      </button>
      {error && <div className="error">{error}</div>}
      {result && (
        <div className="result">
          <div><strong>{result.domain}</strong></div>
          <div>pages crawled: {result.pages_crawled}</div>
          <div>chunks stored: {result.chunks}</div>
          <div className="muted">source_id: {result.source_id}</div>
        </div>
      )}
    </form>
  );
}
