import { useState } from "react";
import { chat } from "../api.js";

const SOURCE_TYPES = [
  { value: "", label: "All sources" },
  { value: "pdf", label: "PDF only" },
  { value: "website", label: "Website only" },
  { value: "database", label: "Database only" },
];

function SqlTrace({ sql }) {
  return (
    <div className="trace-body">
      <div className="muted">
        model={sql.model} · tables offered: {sql.tables_offered.join(", ") || "(none)"} ·{" "}
        {sql.attempts.length} attempt{sql.attempts.length === 1 ? "" : "s"}
      </div>
      {sql.relationships_used && sql.relationships_used.length > 0 && (
        <div className="muted">
          joins given to the model:
          {sql.relationships_used.map((r, i) => (
            <div key={i}><code>{r}</code></div>
          ))}
        </div>
      )}
      {sql.failure_reason && (
        <div className="error">SQL path gave up: {sql.failure_reason} — answered from the table card instead.</div>
      )}
      {sql.attempts.map((a) => (
        <div key={a.n} className="trace-chunk">
          <div className="trace-chunk-head">
            <span className="badge">attempt {a.n}</span>
            {a.gen_ms != null && <span className="muted">generated in {a.gen_ms} ms</span>}
            {(a.error || a.db_error) && <span className="status-down">rejected</span>}
          </div>
          <pre className="chunk">{a.validated_sql || a.raw}</pre>
          {a.error && <div className="muted">validation: {a.error}</div>}
          {a.db_error && <div className="muted">database error: {a.db_error}</div>}
        </div>
      ))}
      {sql.final_sql && (
        <div className="trace-chunk">
          <div className="trace-chunk-head">
            <span className="badge badge-database">executed</span>
            <span className="muted">
              {sql.row_count} row{sql.row_count === 1 ? "" : "s"} · columns: {(sql.columns || []).join(", ")}
            </span>
          </div>
          <pre className="chunk">{sql.final_sql}</pre>
          {sql.phrasing?.result_text && <pre className="chunk">{sql.phrasing.result_text}</pre>}
        </div>
      )}
    </div>
  );
}

function Trace({ trace }) {
  const hasSql = Boolean(trace.sql);
  const [tab, setTab] = useState(hasSql ? "sql" : "retrieval");
  const r = trace.retrieval;
  const g = trace.generation;
  const tabs = ["retrieval", "context", "prompt", "generation", "raw"];
  if (hasSql) tabs.unshift("sql");

  return (
    <div className="trace">
      <div className="trace-tabs">
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            className={tab === t ? "trace-tab active" : "trace-tab"}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "sql" && <SqlTrace sql={trace.sql} />}

      {tab === "retrieval" && (
        <div className="trace-body">
          <div className="muted">
            top_k={r.top_k} · source_type={String(r.source_type)} · {r.duration_ms} ms · {r.chunk_count} chunk
            {r.chunk_count === 1 ? "" : "s"} (lower distance = closer match)
          </div>
          {r.chunks.map((c) => (
            <div key={c.source_id + c.n} className="trace-chunk">
              <div className="trace-chunk-head">
                <span className={`badge badge-${c.source_type}`}>{c.source_type}</span>
                <span>[{c.n}] {c.label}</span>
                <span className="muted">
                  distance {typeof c.distance === "number" ? c.distance.toFixed(4) : "—"}
                </span>
              </div>
              <pre className="chunk">{c.text}</pre>
            </div>
          ))}
        </div>
      )}

      {tab === "context" && (
        <div className="trace-body">
          <div className="muted">The exact context block built from the chunks above.</div>
          <pre className="chunk">{trace.context || "(empty — nothing retrieved)"}</pre>
        </div>
      )}

      {tab === "prompt" && (
        <div className="trace-body">
          <div className="muted">
            model={trace.prompt.model} · options={JSON.stringify(trace.prompt.options)}
          </div>
          {trace.prompt.messages.map((m, i) => (
            <div key={i} className="trace-chunk">
              <div className="trace-chunk-head">
                <span className="badge">{m.role}</span>
              </div>
              <pre className="chunk">{m.content}</pre>
            </div>
          ))}
        </div>
      )}

      {tab === "generation" && (
        <div className="trace-body">
          {g ? (
            <pre className="chunk">{JSON.stringify(g, null, 2)}</pre>
          ) : (
            <div className="muted">Model was not called (no chunks retrieved).</div>
          )}
        </div>
      )}

      {tab === "raw" && (
        <div className="trace-body">
          <div className="muted">Unmodified response object from Ollama.</div>
          <pre className="chunk">{JSON.stringify(trace.raw_response, null, 2) || "null"}</pre>
        </div>
      )}
    </div>
  );
}

export default function ChatPanel() {
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(5);
  const [sourceType, setSourceType] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState([]); // newest first
  const [openTraceIdx, setOpenTraceIdx] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!question.trim() || busy) return;
    const q = question;
    setBusy(true);
    setError(null);
    const startedAt = performance.now();
    try {
      const data = await chat(q, Number(topK), sourceType);
      const roundTripMs = Math.round(performance.now() - startedAt);
      setHistory((h) => [{ q, data, roundTripMs }, ...h]);
      setOpenTraceIdx(null);
      setQuestion("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel chat-panel">
      <h2>Chat</h2>
      <p className="hint">
        Retrieves the top matching chunks from the vector store, then asks the local LLM to answer using only
        those chunks. Every input to the model is in the trace below each answer.
      </p>

      <form onSubmit={handleSubmit} className="chat-form">
        <input
          type="text"
          placeholder="Ask a question about your ingested data…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <div className="row">
          <label>
            Chunks (top_k)
            <input type="number" min={1} max={20} value={topK} onChange={(e) => setTopK(e.target.value)} />
          </label>
          <label>
            Restrict to
            <select value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
              {SOURCE_TYPES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </label>
        </div>
        <button type="submit" disabled={!question.trim() || busy}>
          {busy ? "Thinking… (local LLM, can take a minute)" : "Ask"}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      <div className="chat-history">
        {history.map((item, idx) => (
          <div key={idx} className="chat-turn">
            <div className="chat-q">Q: {item.q}</div>
            <div className="chat-a">
              {(() => {
                const mode = item.data.trace.mode;
                const map = {
                  sql: ["badge-database", "answered by SQL"],
                  sql_fallback_rag: ["badge-website", "SQL failed → table card"],
                  rag: ["", "RAG"],
                };
                const [cls, text] = map[mode] || ["", mode];
                return <span className={`badge ${cls}`} style={{ marginRight: 8 }}>{text}</span>;
              })()}
              {item.data.answer}
              {item.data.grounded === false && <span className="muted"> (not grounded)</span>}
            </div>
            <div className="chat-meta">
              <span className="muted">
                round trip {item.roundTripMs} ms
                {item.data.trace.generation &&
                  ` · gen ${item.data.trace.generation.duration_ms} ms · ${item.data.trace.generation.eval_count} tokens`}
                {` · retrieval ${item.data.trace.retrieval.duration_ms} ms`}
              </span>
              <button
                type="button"
                className="link-button"
                onClick={() => setOpenTraceIdx(openTraceIdx === idx ? null : idx)}
              >
                {openTraceIdx === idx ? "hide trace" : "debug trace"}
              </button>
            </div>
            {openTraceIdx === idx && <Trace trace={item.data.trace} />}
          </div>
        ))}
      </div>
    </section>
  );
}
