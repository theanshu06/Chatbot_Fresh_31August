# Chatbot Fresh — a fully inspectable "chat with your data" system

Ask plain-English questions and get answers grounded in **your own** documents,
websites, and databases — running **100% locally** (no cloud, no API keys, your
data never leaves the machine).

Every answer comes with a **full trace**: you can see exactly what text was
retrieved, what prompt was sent to the model, and — for database questions — the
exact SQL that was generated and run. Nothing is a black box.

---

## 1. The idea, in plain language

Imagine you have:

- a stack of **PDF files** (reports, manuals, policies),
- some **websites** you care about,
- a **database** full of operational records (shift logs, sensor readings, …).

Normally, to get an answer you'd open each one and search manually. This project
builds a single assistant that can answer questions across all three.

It works in two phases:

### Phase A — "Ingestion" (you do this once per source)

You point the system at a PDF / website / database table. It reads everything,
breaks it into bite-sized pieces, and files those pieces into a searchable
**library** (a "vector store"). Think of it as a librarian who reads every
document you give them and memorises where every fact lives.

### Phase B — "Chat" (you do this any time)

You ask a question. The system decides **how** to answer:

| Your question is about… | How it answers | Why |
|---|---|---|
| a PDF or a website | It looks up the most relevant passages and asks a local AI to write an answer **using only those passages**. | Documents are prose — the answer is usually in a paragraph or two. |
| a database table | It writes a **SQL query**, runs it against the live database, and turns the result into a sentence. | Databases hold thousands of near-identical rows. Questions like *"how many…"*, *"which operators…"*, *"total for June"* need a real calculation over **all** the data, not a lucky text match over a handful of rows. |

If the SQL route fails (the AI can't write a valid query, or the database is
unreachable), it **falls back** to a pre-computed summary of the table (the
"table card") so you still get a best-effort answer instead of an error.

---

## 2. The big picture

```
                        ┌──────────────────────────────────────────────────┐
   YOU (browser UI)     │              FRONTEND  (React + Vite)             │
                        │   PDF · Website · Database · Table relationships  │
                        │   Chat panel · "What's been ingested" panel       │
                        └───────────────┬──────────────────────────────────┘
                                        │  HTTP (JSON / file upload)
                        ┌───────────────▼──────────────────────────────────┐
                        │            BACKEND  (FastAPI, Python)             │
                        │                                                  │
   INGESTION            │   /api/ingest/pdf       ─┐                        │
   ────────             │   /api/ingest/website   ─┼─► extract → chunk      │
                        │   /api/ingest/database  ─┘   │   └─► detect table │
                        │                              ▼        joins (FK + │
                        │                     ┌──────────────┐  value overlap)
                        │                     │  Ollama       │             │
                        │                     │  nomic-embed  │  text→vector │
                        │                     └──────┬───────┘              │
                        │                            ▼                      │
                        │                     ┌──────────────┐              │
                        │                     │  ChromaDB     │  local, on   │
                        │                     │  (on disk)   │  disk        │
                        │                     └──────┬───────┘              │
   CHAT                 │   /api/chat ──► retrieve ───┘                     │
   ────                 │        │                                          │
                        │        ├─ PDF / website ──► RAG  (llama3.2:3b)    │
                        │        │                                          │
                        │        └─ database ──► text-to-SQL                │
                        │                 (qwen2.5-coder:3b, given the      │
                        │                  table cards + confirmed joins)   │
                        │                 → run SQL on Postgres (read-only)  │
                        │                 → phrase result (llama3.2:3b)      │
                        │                 → (fallback) table-card RAG        │
                        └──────────────────────────────────────────────────┘
```

---

## 3. How ingestion works, source by source

All three sources end up as **text chunks** in the same ChromaDB collection, each
tagged with metadata (`source_type`, `source_id`, `label`, `kind`) so results can
be filtered and traced back.

### 3.1 PDF  (`ingestion/sources/pdf_source.py`)

1. **Extract** text page-by-page with **`pypdf`** (pure-Python, no system deps).
2. **Chunk**: group **2 pages per chunk** (`PAGES_PER_CHUNK`). Nearby pages stay
   together for context, while each chunk stays small enough for the embedding
   model.
3. Empty pages (scanned/image-only, no text layer) are skipped; a PDF with *no*
   text layer is rejected with a clear message (OCR is out of scope).
4. `source_id = "pdf_" + sha256(file_bytes)[:16]` — re-uploading the same file
   overwrites its old chunks instead of duplicating them.

### 3.2 Website  (`ingestion/sources/website_source.py`)

1. **Crawl** with a **breadth-first search (BFS)** starting from the given URL,
   following only **same-domain** links.
   - Two hard caps, both mandatory: **`max_pages`** (default 20, limit 100) and
     **`max_depth`** (default 2, limit 5). Without them a crawl could wander an
     entire site forever.
   - `requests` for fetching, 10-second timeout per page, custom User-Agent.
2. **Extract** visible text with **BeautifulSoup**, after stripping
   `script / style / nav / footer / header / svg` so boilerplate doesn't pollute
   the text.
3. **Chunk** with a **paragraph-packing algorithm** (`ingestion/chunking.py`):
   split on blank lines, then greedily fill chunks up to a **3000-character
   budget** (`CHUNK_CHAR_BUDGET`) without splitting a paragraph. This keeps
   semantic units intact.
4. `source_id = "web_" + sha256(domain)[:16]`.

### 3.3 Database  (`ingestion/sources/database_source.py`) — Postgres

This path does **more** than the others, because a database question needs to be
answered by *querying*, not by *reading*.

At ingest time, for each selected table:

1. **Row chunks** — pull up to `DB_TABLE_ROW_LIMIT` (5000) rows and format them
   **20 rows per chunk** (`ROWS_PER_CHUNK`), one row per line
   (`Row N: col=val, col=val …`). Kept for transparency and as a fallback
   context source. Tagged `kind: "row_chunk"`.
2. **Schema** — column names, types, and nullability, read from
   `information_schema.columns`.
3. **Table card** (`kind: "table_card"`) — a compact, human-readable summary:
   - total row count,
   - per column: number of distinct values, number of NULLs,
   - for **numeric / date / time** columns: the `min .. max` range,
   - for **low-cardinality text** columns (≤ `TABLE_CARD_MAX_DISTINCT` = 50
     distinct values): **the full list of values**.

   Example card:
   ```
   Table: plant_logs
   Rows: 762
   Columns:
     - id (bigint): 762 distinct, 0 null, range 1 .. 762
     - operator_name (character varying): 14 distinct, 0 null, values: [Amit, Roop, Vinod, Sourav, …]
     - plant_date (date): 118 distinct, 0 null, range 2026-05-05 .. 2026-08-31
     - shift_name (character varying): 4 distinct, 0 null, values: [, A, B, C]
   ```
   This single chunk answers most *"which / what values / how many kinds"*
   questions on its own, and is the fallback when SQL generation fails.
4. **Sample rows** — 5 real rows, stored as few-shot context for the SQL model.
5. **Connection cache** — the Postgres connection parameters (host, port, db,
   user, **password**) are saved to
   `~/.chatbot_fresh_store/db_connections.json`, keyed by `source_id`, so the
   text-to-SQL path can **reconnect at question time**. See
   [Security](#9-security-notes).
6. **Relationship detection** — after every table in the batch is stored, the
   system looks for joins between them (see [§3.4](#34-table-relationships)).

### 3.4 Table relationships  (`ingestion/relationship_detect.py`, `db_relationships.py`)

A question that spans two tables ("*for each shift, compare attendance and plant
activity*") needs a `JOIN`, and a `JOIN` needs to know **which columns connect
the tables**. Postgres foreign keys answer that — but many real databases
(analytics warehouses, CSV imports) don't declare them. So relationships are
discovered and, where uncertain, confirmed by the user.

**At ingest, for every pair of tables in the same database:**

1. **Declared foreign keys** — read from `information_schema`. Stored as
   **`confirmed`** (they're authoritative).
2. **Value-overlap heuristic** — for every pair of same-type **text / date /
   time** columns across the two tables, sample up to `REL_SAMPLE_VALUES` (500)
   distinct values from each and measure **containment**
   `|A ∩ B| / min(|A|, |B|)`. Above `REL_VALUE_OVERLAP_MIN` (0.8) the columns
   probably mean the same thing (e.g. `attendance_date` vs `plant_date` at 96%,
   `shift_name` vs `shift_name` at 100%). Stored as **`suggested`**.
   *Integer columns are excluded* — small-integer ranges collide by coincidence
   far too often (`id` vs `total_manpower`); real integer keys are almost always
   declared FKs anyway.

**In the UI** ("Table relationships" panel) the user sees every suggestion and
clicks **Confirm** or **Dismiss**, and can **add** a join manually
(`table_A.col ↔ table_B.col`, one or more column pairs).

**At question time**, only **`confirmed`** relationships are injected into the
SQL prompt:

```
Known joins between these tables (use these ON conditions for questions that span tables):
- attendance_box_handle.attendance_date = plant_logs.plant_date
  AND attendance_box_handle.shift_name = plant_logs.shift_name
```

Suggested-but-unconfirmed relationships are **never** shown to the model — a
wrong-but-plausible join silently corrupts every answer, so this is opt-in by
design. Relationships are stored in `~/.chatbot_fresh_store/db_relationships.json`
and are cleaned up automatically when a table is deleted.

---

## 4. Storage: the vector store

| Piece | Choice | Reason |
|---|---|---|
| Vector database | **ChromaDB** (`PersistentClient`, on-disk) | Embedded, zero-ops, no server to run, persists to a local folder. Perfect for a single-machine local tool. |
| Similarity metric | **cosine distance** (`hnsw:space = "cosine"`) | Standard for text embeddings — compares *direction* (meaning), not *magnitude*. |
| Index | **HNSW** (Hierarchical Navigable Small World), Chroma's default | Approximate-nearest-neighbour graph: sub-linear search that stays fast as the collection grows, with near-exact recall. |
| One collection for everything | PDF + website + database chunks together, separated by metadata | Lets a single query search across all sources, or filter to one with a `where` clause. |

### Embeddings — **`nomic-embed-text`** (via Ollama)

- **What it does:** converts a piece of text into a **768-dimension vector** such
  that texts with similar meaning land close together.
- **Why this model:**
  - runs **locally** in Ollama (no API, no cost, no data leaving the machine),
  - small (~274 MB) and **CPU-friendly**,
  - purpose-built for **retrieval** (contrastively trained on query↔document
    pairs) and outperforms general-purpose embeddings of the same size on
    retrieval benchmarks (MTEB),
  - 8192-token context (`EMBED_NUM_CTX = 4096` here) comfortably covers our
    chunk sizes.

---

## 5. How a question gets answered  (`ingestion/chat.py`)

### Step 1 — Retrieval (always happens)

1. Embed the question with `nomic-embed-text`.
2. Query ChromaDB for the **top-K** closest chunks (`top_k`, default 5,
   configurable per request; optional `source_type` filter).
3. This also **decides the route**: if `source_type == "database"`, or the
   single closest chunk belongs to a database source, the question goes to the
   text-to-SQL path.

### Step 2a — RAG path  (PDF / website)

> **RAG = Retrieval-Augmented Generation**: retrieve relevant text, then let a
> language model generate an answer *from that text only*.

- Build a numbered **context block** from the retrieved chunks.
- Send to **`llama3.2:3b`** (via Ollama) with a strict system prompt:
  *"Answer using ONLY the numbered context passages. If the answer isn't there,
  reply exactly: I don't know. … add the passage numbers you used, e.g.
  (sources: 1, 2)."*
- Decoding options: `temperature = 0` (deterministic), `num_ctx = 8192`,
  `num_predict = 400` (answer length cap). `keep_alive = 30m` keeps the model
  resident between questions.
- If **nothing** was retrieved, the model is **not called at all** — the answer
  is a plain "I don't know", and the trace still shows what *would* have been
  sent.

**Why `llama3.2:3b`:** a small (2 GB, 3.2 B-parameter) instruction-tuned model
that runs in ~10–30 s per answer on CPU, follows the "use only this context"
instruction reliably, and writes clean prose. Larger models are markedly slower
on CPU for little gain on this grounded-summarisation task.

### Step 2b — Text-to-SQL path  (database)  `ingestion/sql_answer.py`

1. **Generate SQL.** Send the table card(s) + schema + sample rows + the question
   to **`qwen2.5-coder:3b`** with a strict system prompt:
   *"Write exactly ONE read-only SQL query. Must start with SELECT or WITH. Never
   INSERT/UPDATE/DELETE/DDL. Prefer aggregates. If it can't be answered from
   these tables, reply exactly: INSUFFICIENT."*
   All tables from the same database are offered together, and any **confirmed
   relationships** between them ([§3.4](#34-table-relationships)) are appended as
   explicit `JOIN … ON` conditions, so multi-table questions join correctly
   instead of the model guessing the key.
2. **Validate** the generated query (defence in depth — we never trust the model
   output):
   - strip markdown fences, drop a trailing `;`,
   - must match `^(select|with)\b`,
   - **reject** if it contains `insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|merge|vacuum|analyze`,
   - **reject** multiple statements (any inner `;`),
   - if there's no `LIMIT`, **force-append `LIMIT 200`** (`SQL_ROW_LIMIT`).
3. **Execute** on a **fresh read-only connection**
   (`SET SESSION CHARACTERISTICS … READ ONLY`, `autocommit`) with
   `statement_timeout = 5000 ms` (`SQL_STATEMENT_TIMEOUT_MS`).
4. **Retry on error.** If Postgres rejects the query, feed the **exact error
   message** back to the model and ask for a correction — up to
   `SQL_MAX_RETRIES` (2) more attempts.
5. **Phrase the answer.** Hand the result rows (rendered as a small labelled
   table, capped at `SQL_RESULT_CHARS_MAX`) + the original question to
   **`llama3.2:3b`** — *not* the coder model — to write the final sentence.
6. Return a full `sql` sub-trace: every prompt, every candidate query, every
   validation/DB error, the executed query, and the rows.

### Step 2c — Fallback (Option C)

The SQL path returns "not successful" when: the model says `INSUFFICIENT`, all
retries are exhausted, or there are **no cached credentials** for the table.
In that case `chat.py` answers with the **normal RAG prompt** over the table's
**card + row chunks** — a best-effort answer instead of a hard failure.
`trace.mode` is then `"sql_fallback_rag"`.

### The three modes you'll see in the UI

| `trace.mode` | Meaning |
|---|---|
| `sql` | Answered by a live SQL query. |
| `sql_fallback_rag` | SQL generation failed → answered from the table card. |
| `rag` | Normal retrieval + generation (PDF / website, or DB with nothing ingested). |

---

## 6. Models & algorithms — summary and rationale

| Role | Model / algorithm | Size | Why this one |
|---|---|---|---|
| **Text embeddings** | `nomic-embed-text` (Ollama) | ~274 MB | Retrieval-specialised, local, CPU-fast, strong MTEB retrieval scores for its size. |
| **Vector search** | HNSW approximate nearest neighbour + **cosine** distance (ChromaDB) | — | Fast sub-linear search with near-exact recall; cosine matches how text embeddings encode meaning. |
| **PDF chunking** | Fixed **2 pages/chunk** | — | Natural document unit; keeps adjacent pages together; predictable size. |
| **Website chunking** | **Paragraph-packing** to a 3000-char budget | — | Never splits a paragraph; preserves semantic units; keeps chunks embed-sized. |
| **Website crawl** | **BFS**, same-domain, capped by pages + depth | — | Predictable, bounded, can't run away; BFS reaches the most-linked (usually most important) pages first. |
| **DB row chunking** | **20 rows/chunk**, row-per-line | — | Consistent structure for the embedder; used for transparency + fallback only. |
| **DB summary** | **Table card** (counts, ranges, distinct-value lists) computed with SQL aggregates | — | Answers "which/how many/what values" without an LLM; the safety net when SQL fails. |
| **Table-join discovery** | declared **foreign keys** + a **value-overlap** heuristic (Jaccard-style containment on sampled distinct values; text/date/time only) | — | FKs are exact but often absent in real data; value overlap catches `plant_date`↔`reading_date`. No model needed — it's set arithmetic. Suggestions require user confirmation before the SQL model sees them. |
| **SQL generation** | `qwen2.5-coder:3b` (Ollama) | ~1.9 GB | Code-tuned model, current and maintained, strong at PostgreSQL, similar speed to a 3 B general model on CPU. Can also emit `INSUFFICIENT`. Chosen over a 7 B coder (too slow on CPU) and over SQL-only models like `sqlcoder` (older, inflexible). |
| **Answer / result phrasing** | `llama3.2:3b` (Ollama) | ~2.0 GB | Reliable at "answer using only this context", clean prose, fast on CPU. Used for both the RAG answer and phrasing SQL results — the coder model **hallucinated** on the narration step (invented numbers), so the two jobs are split by strength. |
| **SQL safety** | Allow-list validation + forced `LIMIT` + read-only connection + `statement_timeout` + retry-on-error | — | The model's output is never trusted; multiple independent guards. |
| **Generation determinism** | `temperature = 0` everywhere | — | Same question → same answer; makes the trace reproducible and debuggable. |

### Why two answer strategies instead of one?

Pure RAG over embedded rows was the original design and it **fails on tabular
data**: a table split into ~40 chunks, with `top_k = 5`, means the model sees
~12% of the rows. It would confidently answer *"Amit has 6 entries"* when the
real count is 281. Semantic similarity also barely discriminates between 40
near-identical row-blocks. Structured data needs a **query**, so:

- **unstructured (PDF, website) → RAG**
- **structured (database) → text-to-SQL**, with the table card as a safety net.

This is the standard industry split.

---

## 7. Tech stack & running it

### Prerequisites (one-time)

1. **Install [Ollama](https://ollama.com/download)** and pull the three models:
   ```bash
   ollama pull nomic-embed-text
   ollama pull llama3.2:3b
   ollama pull qwen2.5-coder:3b
   ```
2. **Python 3.11+** and **Node 18+**.

### Backend

```bash
pip install -r requirements.txt
uvicorn app:app --port 8100
```
FastAPI docs at `http://localhost:8100/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5174
```

The frontend talks to `http://localhost:8100` by default (override with
`VITE_API_BASE`).

### Backend dependencies (`requirements.txt`)

`fastapi`, `uvicorn[standard]` (web server) · `python-multipart` (file upload) ·
`pydantic-settings`, `python-dotenv` (config) · `ollama` (local models) ·
`chromadb` (vector store) · `pypdf` (PDF text) · `psycopg2-binary` (Postgres) ·
`pandas` (row formatting) · `requests`, `beautifulsoup4` (web crawl).

### Configuration (`ingestion/config.py`, all overridable via `.env`)

| Setting | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `EMBED_MODEL` | `nomic-embed-text` | embedding model |
| `CHAT_MODEL` | `llama3.2:3b` | RAG answers + SQL result phrasing |
| `SQL_MODEL` | `qwen2.5-coder:3b` | SQL generation |
| `CHAT_TOP_K_DEFAULT` | `5` | chunks retrieved per question |
| `CHAT_NUM_CTX` / `CHAT_MAX_TOKENS` | `8192` / `400` | context window / answer cap |
| `SQL_MAX_RETRIES` | `2` | SQL re-prompts on DB error |
| `SQL_ROW_LIMIT` | `200` | forced `LIMIT` on generated queries |
| `SQL_STATEMENT_TIMEOUT_MS` | `5000` | per-query timeout |
| `CHUNK_CHAR_BUDGET` | `3000` | website chunk size |
| `PAGES_PER_CHUNK` / `ROWS_PER_CHUNK` | `2` / `20` | PDF / DB chunk size |
| `TABLE_CARD_MAX_DISTINCT` | `50` | list values below this cardinality |
| `REL_VALUE_OVERLAP_MIN` | `0.8` | value-containment needed to suggest a table join |
| `REL_SAMPLE_VALUES` | `500` | distinct values sampled per column for overlap |
| `CRAWL_MAX_PAGES_*` / `CRAWL_MAX_DEPTH_*` | 20/100, 2/5 | crawl caps |
| `CHROMA_DIR` | `~/.chatbot_fresh_store` | vector store location |
| `DB_CONN_FILE` | `<CHROMA_DIR>/db_connections.json` | cached Postgres credentials |
| `DB_REL_FILE` | `<CHROMA_DIR>/db_relationships.json` | table join relationships |

---

## 8. API reference

| Method & path | Body | Purpose |
|---|---|---|
| `GET /api/health` | — | liveness check |
| `POST /api/ingest/pdf` | multipart `file` | ingest a PDF |
| `POST /api/ingest/website` | `{url, max_pages, max_depth}` | crawl + ingest a site |
| `POST /api/database/tables` | `{host, port, dbname, user, password}` | list tables (no ingest) — powers the UI checklist |
| `POST /api/ingest/database` | `… , tables: [...]` | ingest selected tables (builds card, caches connection, detects relationships) |
| `POST /api/chat` | `{question, top_k?, source_type?}` | **ask a question** — returns `{answer, grounded, trace}` |
| `POST /api/search` | `{query, top_k?, source_type?}` | raw retrieval only (verification/debug) |
| `GET /api/database/tables-ingested` | — | ingested DB tables + their columns (powers the relationship form) |
| `GET /api/relationships` | — | all table join relationships (confirmed + suggested) |
| `POST /api/relationships` | `{left/right_source_id, left/right_table, joins:[{left,right}], note?}` | declare a join manually (stored confirmed) |
| `POST /api/relationships/{id}/confirm` | — | promote a suggested relationship to confirmed |
| `DELETE /api/relationships/{id}` | — | remove a relationship |
| `GET /api/ingested` | — | every stored source + every chunk (the "Show" panel) |
| `DELETE /api/ingested` | — | wipe everything (also clears cached DB credentials + relationships) |
| `DELETE /api/ingested/{source_id}` | — | remove one source (also forgets its credentials + relationships) |

---

## 9. Security notes

- **Everything runs locally.** No document, question, or answer is sent to any
  external service. The only network calls are: Ollama on `localhost`, the
  website crawler (to sites you point it at), and Postgres (to the database you
  connect).
- **Cached database credentials.** To answer database questions, the system must
  reconnect to Postgres *after* ingest, so it stores the connection parameters —
  **including the password, in plaintext** — in
  `~/.chatbot_fresh_store/db_connections.json` (permissions set to `0600` where
  the OS supports it). This is a deliberate trade-off for a **local,
  single-user** tool. Deleting the file (or deleting the source in the UI, or
  `DELETE /api/ingested`) revokes it; the database path then falls back to the
  table card.
- **SQL execution is read-only and guarded**: allow-list validation, forced
  `LIMIT`, read-only connection, statement timeout. The generated SQL is always
  visible in the trace before you trust the answer.
- `.env` and the data store are git-ignored.

---

## 10. Known limitations

- **Speed.** Ollama here is **CPU-only**: expect **1–2 minutes** per answer (model
  load + generation). Keep prompts small; the model stays resident for 30 min
  between questions.
- **Scanned PDFs** (no text layer) can't be ingested — no OCR.
- **Postgres only** for the database source.
- **Small local models** make mistakes: SQL for complex multi-JOIN analytics may
  need a retry or come back `INSUFFICIENT`. The trace shows every attempt. Note
  that the SQL model tends to *degrade* across retries as the error context grows
  — a hard aggregation over a many-to-many join (fan-out double counting) is near
  the edge of what a 3B model does reliably.
- **Relationship detection is best-effort.** Declared foreign keys are exact;
  the value-overlap heuristic only *suggests* (integer keys aren't guessed at
  all). Cross-table questions work best once you've confirmed the right joins in
  the "Table relationships" panel.
- The **table card** lists distinct values only for columns with ≤ 50 distinct
  values; a novel aggregate the card didn't pre-compute still needs the SQL path.
- Answer quality on messy source data is bounded by the data (e.g. a `plant_logs`
  table with `operator_name` values `Roop` / `roop` / `Mustjab malik` /
  `Mustjab Malik` will count them separately — that's the data, not the system).

---

## 11. Project layout

```
app.py                      FastAPI app + CORS + router mount
requirements.txt
.env.example

ingestion/
  config.py                 all settings (Pydantic BaseSettings)
  routes.py                 all HTTP endpoints
  models.py                 request schemas (Pydantic)
  pipeline.py               orchestrator: extract → chunk → embed → store
  chunking.py               generic paragraph-packing chunker (website)
  vector_store.py           ChromaDB wrapper: embed, store, search, list, delete
  chat.py                   retrieval + routing + RAG generation + fallback
  sql_answer.py             text-to-SQL: generate → validate → run → retry → phrase
  db_connections.py         cache of Postgres credentials, keyed by source_id
  db_relationships.py       store of table join relationships (confirmed/suggested)
  relationship_detect.py    FK + value-overlap discovery, run at ingest
  sources/
    pdf_source.py           pypdf → page chunks
    website_source.py       BFS crawl + BeautifulSoup → text chunks
    database_source.py      schema, table card, row chunks, FKs, read-only query runner

frontend/                   React + Vite
  src/
    api.js                  typed fetch wrappers for every endpoint
    App.jsx                 layout: 3 ingest panels + relationships + chat + "show ingested"
    components/
      PdfPanel.jsx  WebsitePanel.jsx  DatabasePanel.jsx
      RelationshipsPanel.jsx  confirm/dismiss/add table joins
      ChatPanel.jsx         question box + answer history + the debug Trace
      ShowIngested.jsx      browse / delete every stored chunk
```
