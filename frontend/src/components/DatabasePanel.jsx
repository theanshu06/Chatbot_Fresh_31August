import { useState } from "react";
import { ingestDatabase, listDatabaseTables } from "../api.js";

export default function DatabasePanel({ onIngested }) {
  const [form, setForm] = useState({ host: "localhost", port: "5432", dbname: "", user: "", password: "" });
  const [tables, setTables] = useState(null); // null = not connected yet, [] = connected, no tables
  const [selected, setSelected] = useState(new Set());
  const [connectBusy, setConnectBusy] = useState(false);
  const [connectError, setConnectError] = useState(null);
  const [ingestBusy, setIngestBusy] = useState(false);
  const [ingestError, setIngestError] = useState(null);
  const [result, setResult] = useState(null);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
    // connection fields changed — the table list we're showing no longer
    // necessarily matches where ingestion would actually go, so require a fresh connect
    setTables(null);
    setSelected(new Set());
    setResult(null);
  }

  const connParams = { host: form.host, port: form.port, dbname: form.dbname, user: form.user, password: form.password };

  async function handleConnect(e) {
    e.preventDefault();
    setConnectBusy(true);
    setConnectError(null);
    setResult(null);
    try {
      const data = await listDatabaseTables(connParams);
      setTables(data.tables);
      setSelected(new Set(data.tables)); // select all by default
    } catch (err) {
      setConnectError(err.message);
      setTables(null);
    } finally {
      setConnectBusy(false);
    }
  }

  function toggleTable(name) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => (prev.size === tables.length ? new Set() : new Set(tables)));
  }

  async function handleIngest() {
    setIngestBusy(true);
    setIngestError(null);
    setResult(null);
    try {
      const data = await ingestDatabase({ ...connParams, tables: Array.from(selected) });
      setResult(data);
      onIngested?.();
    } catch (err) {
      setIngestError(err.message);
    } finally {
      setIngestBusy(false);
    }
  }

  return (
    <div className="panel">
      <h2>Database</h2>
      <p className="hint">Connect to Postgres, pick which tables to embed, then ingest just those.</p>

      <form onSubmit={handleConnect} className="db-connect-form">
        <div className="row">
          <label>Host<input value={form.host} onChange={(e) => update("host", e.target.value)} required /></label>
          <label>Port<input value={form.port} onChange={(e) => update("port", e.target.value)} required /></label>
        </div>
        <label>Database name<input value={form.dbname} onChange={(e) => update("dbname", e.target.value)} required /></label>
        <div className="row">
          <label>User<input value={form.user} onChange={(e) => update("user", e.target.value)} required /></label>
          <label>Password<input type="password" value={form.password} onChange={(e) => update("password", e.target.value)} required /></label>
        </div>
        <button type="submit" disabled={connectBusy}>
          {connectBusy ? "Connecting…" : "Connect & list tables"}
        </button>
      </form>
      {connectError && <div className="error">{connectError}</div>}

      {tables && tables.length === 0 && <p className="muted">Connected — no tables found in the public schema.</p>}

      {tables && tables.length > 0 && (
        <div className="table-select">
          <div className="table-select-header">
            <span className="muted">{tables.length} table{tables.length === 1 ? "" : "s"} found</span>
            <button type="button" className="link-button" onClick={toggleAll}>
              {selected.size === tables.length ? "Deselect all" : "Select all"}
            </button>
          </div>
          <div className="table-list">
            {tables.map((t) => (
              <label key={t} className="table-item">
                <input type="checkbox" checked={selected.has(t)} onChange={() => toggleTable(t)} />
                <span>{t}</span>
              </label>
            ))}
          </div>
          <button type="button" onClick={handleIngest} disabled={selected.size === 0 || ingestBusy}>
            {ingestBusy ? "Ingesting…" : `Ingest ${selected.size || ""} selected table${selected.size === 1 ? "" : "s"}`}
          </button>
        </div>
      )}

      {ingestError && <div className="error">{ingestError}</div>}
      {result && (
        <div className="result">
          <div>total chunks stored: {result.total_chunks}</div>
          <ul>
            {result.tables_ingested.map((t) => (
              <li key={t.table}>{t.table} — {t.chunks} chunks ({t.total_rows} rows)</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
