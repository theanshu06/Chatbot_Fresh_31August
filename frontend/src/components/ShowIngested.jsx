import { useState, forwardRef, useImperativeHandle } from "react";
import { getIngested, clearIngested, deleteSource } from "../api.js";

const TYPE_LABELS = { pdf: "PDF", website: "Website", database: "Database" };

const ShowIngested = forwardRef(function ShowIngested(_props, ref) {
  const [sources, setSources] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [pendingId, setPendingId] = useState(null);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      const data = await getIngested();
      setSources(data.sources);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  useImperativeHandle(ref, () => ({ refresh }));

  async function handleClearAll() {
    if (!window.confirm("Delete ALL ingested data from the vector store?")) return;
    setBusy(true);
    setError(null);
    try {
      await clearIngested();
      await refresh();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  async function handleDelete(source) {
    if (!window.confirm(`Delete "${source.label}" and its ${source.chunk_count} chunk${source.chunk_count === 1 ? "" : "s"}?`)) return;
    setPendingId(source.source_id);
    setError(null);
    try {
      await deleteSource(source.source_id);
      if (openId === source.source_id) setOpenId(null);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setPendingId(null);
    }
  }

  return (
    <section className="panel show-panel">
      <h2>What's been ingested</h2>
      <p className="hint">
        Every chunk below is exactly what was extracted from your source (the PDF page, the crawled page, the
        database rows) — and it's also exactly the text that got embedded into the vector store. There's no
        separate "raw" copy; this is both at once.
      </p>
      <div className="row">
        <button type="button" onClick={refresh} disabled={busy}>
          {busy ? "Loading…" : "Show ingested data"}
        </button>
        {sources && sources.length > 0 && (
          <button type="button" className="danger" onClick={handleClearAll} disabled={busy}>
            Clear all
          </button>
        )}
      </div>
      {error && <div className="error">{error}</div>}

      {sources && sources.length === 0 && <p className="muted">Nothing ingested yet.</p>}

      {sources && sources.length > 0 && (
        <div className="source-list">
          {sources.map((s) => (
            <div key={s.source_id} className="source-card">
              <div className="source-header-row">
                <button
                  type="button"
                  className="source-header"
                  onClick={() => setOpenId(openId === s.source_id ? null : s.source_id)}
                >
                  <span className={`badge badge-${s.source_type}`}>{TYPE_LABELS[s.source_type] || s.source_type}</span>
                  <span className="source-label">{s.label}</span>
                  <span className="muted">{s.chunk_count} chunk{s.chunk_count === 1 ? "" : "s"}</span>
                  <span className="chevron">{openId === s.source_id ? "▲" : "▼"}</span>
                </button>
                <button
                  type="button"
                  className="danger source-delete"
                  onClick={() => handleDelete(s)}
                  disabled={pendingId === s.source_id || busy}
                >
                  {pendingId === s.source_id ? "Deleting…" : "Delete"}
                </button>
              </div>
              {openId === s.source_id && (
                <div className="chunk-list">
                  {[...s.chunks]
                    .sort((a, b) => (a.kind === "table_card" ? -1 : 0) - (b.kind === "table_card" ? -1 : 0))
                    .map((c, i) => (
                      <pre key={c.id} className="chunk">
                        <div className="chunk-index">
                          {c.kind === "table_card"
                            ? "table card (schema + stats — used for text-to-SQL and as fallback)"
                            : `chunk ${i + 1} of ${s.chunks.length}`}
                        </div>
                        {c.text}
                      </pre>
                    ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
});

export default ShowIngested;
