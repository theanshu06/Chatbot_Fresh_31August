"""RAG answer step, with a text-to-SQL fast path for database questions.

Routing (see `answer`):
    - question aimed at a database source  -> try_sql (Option A). On success the
      answer comes straight from a live SQL query. On failure fall back to the
      table card + row chunks (Option C) through the normal RAG prompt.
    - question aimed at PDF / website       -> normal RAG, unchanged.

Everything that goes into every model call is returned in `trace` so any answer
can be debugged end to end:
    trace.mode        -- "sql" | "sql_fallback_rag" | "rag"
    trace.retrieval   -- what was pulled from the vector store, with distances
    trace.context     -- the exact context block built from those chunks
    trace.prompt      -- the verbatim messages + model + options sent to Ollama
    trace.generation  -- timings and token counts
    trace.raw_response-- the full, unmodified Ollama response
    trace.sql         -- (sql / sql_fallback_rag only) every SQL attempt + result
"""

import time

import ollama

from ingestion import sql_answer, vector_store
from ingestion.config import settings

SYSTEM_PROMPT = (
    "You are a question-answering assistant. Answer the user's question using ONLY "
    "the numbered context passages provided below. If the answer is not contained in "
    "the context, reply exactly: I don't know. Do not use outside knowledge.\n"
    "Write the answer as one or more complete sentences. Never reply with only a "
    "citation marker. After the sentence(s), add the passage numbers you used in "
    "parentheses, e.g. (sources: 1, 2)."
)


def _build_context(chunks: list[dict]) -> str:
    """One numbered block per chunk, tagged with where it came from."""
    blocks = []
    for i, c in enumerate(chunks, start=1):
        blocks.append(f"[{i}] (source: {c['label']} - {c['source_type']})\n{c['text']}")
    return "\n\n".join(blocks)


def _jsonable(resp):
    """Ollama returns a pydantic model in recent versions; fall back to dict()."""
    if hasattr(resp, "model_dump"):
        return resp.model_dump(mode="json")
    return dict(resp)


def _retrieval_trace(chunks, top_k, source_type, retrieval_ms) -> dict:
    return {
        "top_k": top_k,
        "source_type": source_type,
        "duration_ms": retrieval_ms,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "n": i + 1,
                "label": c["label"],
                "source_type": c["source_type"],
                "source_id": c["source_id"],
                "distance": c["distance"],
                "text": c["text"],
            }
            for i, c in enumerate(chunks)
        ],
    }


def _rag_answer(question: str, chunks: list[dict], retrieval: dict, *, mode: str, sql_trace: dict | None) -> dict:
    """The original RAG generation step, shared by the plain path and the
    text-to-SQL fallback.
    """
    context = _build_context(chunks)
    user_content = f"Context passages:\n\n{context}\n\n---\n\nQuestion: {question}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    options = {
        "num_ctx": settings.CHAT_NUM_CTX,
        "temperature": settings.CHAT_TEMPERATURE,
        "num_predict": settings.CHAT_MAX_TOKENS,
    }

    trace = {
        "mode": mode,
        "question": question,
        "retrieval": retrieval,
        "context": context,
        "prompt": {"model": settings.CHAT_MODEL, "options": options, "messages": messages},
        "generation": None,
        "raw_response": None,
        "sql": sql_trace,
    }

    if not chunks:
        return {
            "answer": "I don't know - nothing ingested matches this question.",
            "grounded": False,
            "trace": trace,
        }

    t1 = time.perf_counter()
    resp = ollama.chat(
        model=settings.CHAT_MODEL,
        messages=messages,
        options=options,
        keep_alive=settings.CHAT_KEEP_ALIVE,
    )
    generation_ms = round((time.perf_counter() - t1) * 1000, 1)

    data = _jsonable(resp)
    trace["generation"] = {
        "duration_ms": generation_ms,
        "model": data.get("model"),
        "done_reason": data.get("done_reason"),
        "prompt_eval_count": data.get("prompt_eval_count"),
        "eval_count": data.get("eval_count"),
        "total_duration_ns": data.get("total_duration"),
    }
    trace["raw_response"] = data

    return {"answer": data["message"]["content"], "grounded": True, "trace": trace}


def _sql_response(question: str, sql_result: dict, retrieval: dict) -> dict:
    """Wrap a successful try_sql() result in the standard answer/trace envelope."""
    sql_trace = sql_result["sql_trace"]
    last_attempt = sql_trace["attempts"][-1] if sql_trace["attempts"] else {}
    phrasing = sql_trace.get("phrasing") or {}
    trace = {
        "mode": "sql",
        "question": question,
        "retrieval": retrieval,
        "context": phrasing.get("result_text", ""),
        "prompt": {
            "model": phrasing.get("model", settings.CHAT_MODEL),
            "options": {"num_ctx": settings.SQL_NUM_CTX, "temperature": 0.0},
            "messages": phrasing.get("messages", []),
        },
        "generation": {
            "duration_ms": phrasing.get("duration_ms"),
            "model": phrasing.get("model", settings.CHAT_MODEL),
            "sql_model": sql_trace["model"],
            "sql_attempts": len(sql_trace["attempts"]),
            "row_count": sql_trace.get("row_count"),
        },
        "raw_response": last_attempt,
        "sql": sql_trace,
    }
    return {"answer": sql_result["answer"], "grounded": sql_result["grounded"], "trace": trace}


def _wants_sql(source_type: str | None, chunks: list[dict]) -> bool:
    if source_type == "database":
        return True
    if source_type is None and chunks and chunks[0]["source_type"] == "database":
        return True
    return False


def answer(question: str, top_k: int, source_type: str | None) -> dict:
    # 1. Retrieval (also decides routing when source_type is unset)
    t0 = time.perf_counter()
    chunks = vector_store.search(question, top_k=top_k, source_type=source_type)
    retrieval_ms = round((time.perf_counter() - t0) * 1000, 1)
    retrieval = _retrieval_trace(chunks, top_k, source_type, retrieval_ms)

    # 2. Database question -> text-to-SQL, with the table card as the fallback
    if _wants_sql(source_type, chunks):
        tables = vector_store.get_database_tables()
        if tables:
            db_hit = next((c for c in chunks if c["source_type"] == "database"), None)
            primary_source_id = db_hit["source_id"] if db_hit else tables[0]["source_id"]

            sql_result = sql_answer.try_sql(question, tables, primary_source_id)
            if sql_result["success"]:
                return _sql_response(question, sql_result, retrieval)

            # Option C fallback: answer from this table's card + row chunks.
            fallback_chunks = [c for c in chunks if c["source_type"] == "database"]
            if not fallback_chunks:
                fallback_chunks = [
                    {"text": t["card"], "label": t["label"], "source_type": "database",
                     "source_id": t["source_id"], "distance": None}
                    for t in tables if t["source_id"] == primary_source_id
                ]
            return _rag_answer(
                question, fallback_chunks, retrieval,
                mode="sql_fallback_rag", sql_trace=sql_result["sql_trace"],
            )

    # 3. Normal RAG (PDF / website / or database with nothing ingested)
    return _rag_answer(question, chunks, retrieval, mode="rag", sql_trace=None)
