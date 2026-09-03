"""Text-to-SQL answer path for database sources (Option A).

Flow:
    1. Ask SQL_MODEL for ONE read-only SELECT, given the table cards + samples.
    2. Validate it (SELECT/WITH only, no DML/DDL, forced LIMIT).
    3. Run it read-only with a statement timeout.
    4. On a database error, feed the error back and retry (SQL_MAX_RETRIES).
    5. Ask CHAT_MODEL (better at prose) to phrase the result rows into an answer.

try_sql() returns a dict with "success". On success the caller uses the answer
directly; on failure the caller falls back to the table card (Option C). Either
way "sql_trace" records every prompt, candidate query, and error so the whole
attempt is inspectable in the UI.
"""

import re
import time

import ollama
import psycopg2

from ingestion import db_connections, db_relationships
from ingestion.config import settings
from ingestion.sources import database_source

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|merge|vacuum|analyze)\b",
    re.IGNORECASE,
)

_SQL_SYSTEM = (
    "You are a PostgreSQL expert. Given one or more table definitions and a question, "
    "write exactly ONE read-only SQL query that answers it.\n"
    "Rules:\n"
    "- The query MUST start with SELECT or WITH. Never write INSERT, UPDATE, DELETE, or any DDL.\n"
    "- Use only the tables and columns shown. Quote identifiers that need it.\n"
    "- Prefer aggregates (COUNT, SUM, MIN, MAX, GROUP BY) when the question asks about totals, "
    "counts, ranges, or distinct values.\n"
    "- If the question cannot be answered from these tables, reply with exactly: INSUFFICIENT\n"
    "- Output ONLY the SQL (or INSUFFICIENT). No explanation, no markdown code fences."
)

_PHRASE_SYSTEM = (
    "You are given a user's question and the result table of a SQL query that was run to "
    "answer it. Each line under 'rows' is ONE distinct result row; the values are already "
    "correct — just report them.\n"
    "- Answer in one or more complete sentences, listing the relevant rows/values.\n"
    "- Use ONLY the numbers and text in the result. Never invent values.\n"
    "- If there are no rows, say that no matching records were found.\n"
    "- Do not mention SQL, LIMIT, tables, or how the result was produced."
)


def _tables_block(tables: list[dict]) -> str:
    blocks = []
    for t in tables:
        lines = [t["card"]]
        if t.get("sample_rows"):
            lines.append("Sample rows:")
            for r in t["sample_rows"][:5]:
                lines.append("  " + ", ".join(f"{k}={v}" for k, v in r.items()))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _rel_condition(rel: dict) -> str:
    """`plant_logs.plant_date = compressor_readings.reading_date AND ...`"""
    return " AND ".join(
        f"{rel['left_table']}.{j['left']} = {rel['right_table']}.{j['right']}" for j in rel["joins"]
    )


def _relationships_block(rels: list[dict]) -> str:
    if not rels:
        return ""
    lines = ["Known joins between these tables (use these ON conditions for questions that span tables):"]
    lines += [f"- {_rel_condition(r)}" for r in rels]
    return "\n".join(lines) + "\n\n"


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _validate(raw: str) -> tuple[str | None, str | None]:
    """Return (safe_sql, error). Exactly one is non-None."""
    sql = _strip_fences(raw).rstrip(";").strip()
    if not sql:
        return None, "empty query"
    if sql.upper() == "INSUFFICIENT":
        return None, "INSUFFICIENT"
    if ";" in sql:
        return None, "multiple statements are not allowed"
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        return None, "query must start with SELECT or WITH"
    if _FORBIDDEN.search(sql):
        return None, "query contains a forbidden (non read-only) keyword"
    if not re.search(r"\blimit\b", sql, re.IGNORECASE):
        sql = f"{sql}\nLIMIT {settings.SQL_ROW_LIMIT}"
    return sql, None


def _cell(v) -> str:
    if v is None:
        return "NULL"
    if v == "":
        return "(empty string)"
    return str(v)


def _render_result(columns: list[str], rows: list[list]) -> str:
    head = f"{len(rows)} row(s) returned. columns: [{', '.join(columns)}]"
    body = "\n".join(
        "  - " + ", ".join(f"{col}={_cell(v)}" for col, v in zip(columns, row))
        for row in rows
    )
    text = head + ("\nrows:\n" + body if body else "\n(no rows)")
    if len(text) > settings.SQL_RESULT_CHARS_MAX:
        text = text[: settings.SQL_RESULT_CHARS_MAX] + "\n… (result truncated)"
    return text


def _chat(messages: list[dict], model: str) -> str:
    resp = ollama.chat(
        model=model,
        messages=messages,
        options={"num_ctx": settings.SQL_NUM_CTX, "temperature": 0.0},
        keep_alive=settings.CHAT_KEEP_ALIVE,
    )
    data = resp.model_dump(mode="json") if hasattr(resp, "model_dump") else dict(resp)
    return data["message"]["content"]


def try_sql(question: str, tables: list[dict], primary_source_id: str) -> dict:
    trace: dict = {
        "model": settings.SQL_MODEL,
        "tables_offered": [t["label"] for t in tables],
        "relationships_used": [],
        "attempts": [],
        "final_sql": None,
        "columns": None,
        "row_count": None,
        "phrasing": None,
        "failure_reason": None,
    }

    primary = next((t for t in tables if t["source_id"] == primary_source_id), tables[0] if tables else None)
    if primary is None:
        trace["failure_reason"] = "no ingested database tables"
        return {"success": False, "sql_trace": trace}

    conn_params = db_connections.get(primary["source_id"])
    if not conn_params:
        trace["failure_reason"] = (
            f"no cached credentials for table '{primary['label']}' — re-ingest it to enable SQL"
        )
        return {"success": False, "sql_trace": trace}

    # Offer the model every table that lives in the same database (join context).
    join_tables = [
        t for t in tables
        if (db_connections.get(t["source_id"]) or {}).get("dbname") == conn_params.get("dbname")
    ] or [primary]
    trace["tables_offered"] = [t["label"] for t in join_tables]

    # Confirmed join relationships between the offered tables — the only hint the
    # model gets about how to connect them.
    offered_sids = {t["source_id"] for t in join_tables}
    rels = db_relationships.for_source_ids(offered_sids, only_confirmed=True)
    rel_block = _relationships_block(rels)
    trace["relationships_used"] = [_rel_condition(r) for r in rels]

    user_prompt = f"{_tables_block(join_tables)}\n\n{rel_block}---\n\nQuestion: {question}"
    messages = [
        {"role": "system", "content": _SQL_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    safe_sql = None
    columns: list[str] = []
    rows: list[list] = []

    for attempt in range(settings.SQL_MAX_RETRIES + 1):
        t0 = time.perf_counter()
        raw = _chat(messages, settings.SQL_MODEL)
        gen_ms = round((time.perf_counter() - t0) * 1000, 1)
        candidate, err = _validate(raw)
        record = {"n": attempt + 1, "raw": raw, "validated_sql": candidate, "gen_ms": gen_ms, "error": err}

        if err == "INSUFFICIENT":
            trace["attempts"].append(record)
            trace["failure_reason"] = "model judged the question unanswerable from these tables"
            return {"success": False, "sql_trace": trace}
        if err:
            trace["attempts"].append(record)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"That query was rejected: {err}. Return a corrected query."},
            ]
            continue

        try:
            columns, rows = database_source.run_readonly_query(conn_params, candidate)
            record["db_error"] = None
            trace["attempts"].append(record)
            safe_sql = candidate
            break
        except (psycopg2.Error, psycopg2.Warning) as e:
            db_err = str(e).strip()
            record["db_error"] = db_err
            trace["attempts"].append(record)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"Running that query failed with: {db_err}. Return a corrected query."},
            ]

    if safe_sql is None:
        trace["failure_reason"] = "no valid query after retries"
        return {"success": False, "sql_trace": trace}

    trace["final_sql"] = safe_sql
    trace["columns"] = columns
    trace["row_count"] = len(rows)

    result_text = _render_result(columns, rows)
    phrase_messages = [
        {"role": "system", "content": _PHRASE_SYSTEM},
        {"role": "user", "content": f"Question: {question}\n\n{result_text}"},
    ]
    t0 = time.perf_counter()
    answer_text = _chat(phrase_messages, settings.CHAT_MODEL).strip()
    trace["phrasing"] = {
        "model": settings.CHAT_MODEL,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "result_text": result_text,
        "messages": phrase_messages,
    }

    return {
        "success": True,
        "answer": answer_text,
        "grounded": len(rows) > 0,
        "sql_trace": trace,
    }
