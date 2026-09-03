import { useState, useEffect, forwardRef, useImperativeHandle, useCallback } from "react";
import {
  getRelationships,
  getIngestedDbTables,
  addRelationship,
  confirmRelationship,
  deleteRelationship,
} from "../api.js";

function joinText(rel) {
  return rel.joins
    .map((j) => `${rel.left_table}.${j.left} = ${rel.right_table}.${j.right}`)
    .join("  AND  ");
}

const RelationshipsPanel = forwardRef(function RelationshipsPanel(_props, ref) {
  const [rels, setRels] = useState([]);
  const [tables, setTables] = useState([]);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  // manual "add" form
  const [leftId, setLeftId] = useState("");
  const [rightId, setRightId] = useState("");
  const [pairs, setPairs] = useState([{ left: "", right: "" }]);
  const [note, setNote] = useState("");
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [r, t] = await Promise.all([getRelationships(), getIngestedDbTables()]);
      setRels(r.relationships);
      setTables(t.tables);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useImperativeHandle(ref, () => ({ refresh }));
  useEffect(() => {
    refresh();
  }, [refresh]);

  const leftTable = tables.find((t) => t.source_id === leftId);
  const rightTable = tables.find((t) => t.source_id === rightId);

  const confirmed = rels.filter((r) => r.status === "confirmed");
  const suggested = rels.filter((r) => r.status === "suggested");

  async function act(fn, id) {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  async function submitManual(e) {
    e.preventDefault();
    if (!leftTable || !rightTable) return;
    const cleanPairs = pairs.filter((p) => p.left && p.right);
    if (cleanPairs.length === 0) {
      setError("Pick at least one column pair.");
      return;
    }
    setAdding(true);
    setError(null);
    try {
      await addRelationship({
        left_source_id: leftTable.source_id,
        right_source_id: rightTable.source_id,
        left_table: leftTable.label,
        right_table: rightTable.label,
        joins: cleanPairs,
        note: note || "declared manually",
      });
      setPairs([{ left: "", right: "" }]);
      setNote("");
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setAdding(false);
    }
  }

  return (
    <section className="panel show-panel">
      <h2>Table relationships</h2>
      <p className="hint">
        How your database tables connect. Only <strong>confirmed</strong> joins are given to the SQL
        model — so cross-table questions ("for each shift, compare X and Y") join correctly.
        Suggestions below are auto-detected from matching column values; confirm the right ones.
      </p>

      {error && <div className="error">{error}</div>}

      {tables.length < 2 && (
        <p className="muted">Ingest at least two tables from the same database to define relationships.</p>
      )}

      {confirmed.length > 0 && (
        <div className="rel-group">
          <div className="muted">Confirmed — used by the SQL model</div>
          {confirmed.map((r) => (
            <div key={r.id} className="rel-row">
              <span className="badge badge-database">{r.source === "foreign_key" ? "FK" : r.source}</span>
              <code className="rel-join">{joinText(r)}</code>
              <button
                type="button"
                className="danger"
                disabled={busyId === r.id}
                onClick={() => act(() => deleteRelationship(r.id), r.id)}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {suggested.length > 0 && (
        <div className="rel-group">
          <div className="muted">Suggested — not used until confirmed</div>
          {suggested.map((r) => (
            <div key={r.id} className="rel-row">
              <span className="badge">guess</span>
              <div className="rel-join-col">
                <code className="rel-join">{joinText(r)}</code>
                {r.note && <span className="muted">{r.note}</span>}
              </div>
              <button
                type="button"
                disabled={busyId === r.id}
                onClick={() => act(() => confirmRelationship(r.id), r.id)}
              >
                Confirm
              </button>
              <button
                type="button"
                className="danger"
                disabled={busyId === r.id}
                onClick={() => act(() => deleteRelationship(r.id), r.id)}
              >
                Dismiss
              </button>
            </div>
          ))}
        </div>
      )}

      {tables.length >= 2 && (
        <form onSubmit={submitManual} className="rel-form">
          <div className="muted">Add a relationship manually</div>
          <div className="row">
            <label>
              Left table
              <select value={leftId} onChange={(e) => setLeftId(e.target.value)}>
                <option value="">—</option>
                {tables.map((t) => (
                  <option key={t.source_id} value={t.source_id}>{t.label}</option>
                ))}
              </select>
            </label>
            <label>
              Right table
              <select value={rightId} onChange={(e) => setRightId(e.target.value)}>
                <option value="">—</option>
                {tables.map((t) => (
                  <option key={t.source_id} value={t.source_id}>{t.label}</option>
                ))}
              </select>
            </label>
          </div>

          {leftTable && rightTable && (
            <>
              {pairs.map((p, i) => (
                <div className="row" key={i}>
                  <label>
                    {i === 0 ? "Join on" : "and"}
                    <select
                      value={p.left}
                      onChange={(e) =>
                        setPairs((ps) => ps.map((x, j) => (j === i ? { ...x, left: e.target.value } : x)))
                      }
                    >
                      <option value="">— {leftTable.label} column —</option>
                      {leftTable.columns.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    equals
                    <select
                      value={p.right}
                      onChange={(e) =>
                        setPairs((ps) => ps.map((x, j) => (j === i ? { ...x, right: e.target.value } : x)))
                      }
                    >
                      <option value="">— {rightTable.label} column —</option>
                      {rightTable.columns.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </label>
                </div>
              ))}
              <button
                type="button"
                className="link-button"
                onClick={() => setPairs((ps) => [...ps, { left: "", right: "" }])}
              >
                + another column pair
              </button>
              <label>
                Note (optional)
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. same day + shift" />
              </label>
              <button type="submit" disabled={adding}>
                {adding ? "Adding…" : "Add relationship"}
              </button>
            </>
          )}
        </form>
      )}
    </section>
  );
});

export default RelationshipsPanel;
