"""Postgres table -> text chunks. Pulls actual rows (capped at
DB_TABLE_ROW_LIMIT) and formats them as text, the same row-per-line shape
used elsewhere in this project so the embedding model sees consistent
structure across sources.

Also provides the pieces the text-to-SQL path needs at question time:
    get_schema()          -- column names + types + nullability
    sample_rows()         -- a few real rows, for few-shot prompting
    build_table_card()    -- row count + per-column stats (Option C fallback)
    run_readonly_query()  -- execute a generated SELECT, read-only, time-boxed

...and the pieces relationship detection needs at ingest time:
    get_foreign_keys()    -- declared FK constraints among a set of tables
    sample_columns()      -- distinct values per column, for value-overlap matching
"""

import hashlib

import pandas as pd
import psycopg2
from psycopg2 import sql as pg_sql

from ingestion.config import settings

_NUMERIC_TYPES = {
    "smallint", "integer", "bigint", "decimal", "numeric",
    "real", "double precision", "money",
}
_TEMPORAL_TYPES = {
    "date", "time", "time without time zone", "time with time zone",
    "timestamp", "timestamp without time zone", "timestamp with time zone",
}


def _connect(conn_params: dict):
    conn = psycopg2.connect(**conn_params)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def list_table_names(conn_params: dict) -> list[str]:
    conn = psycopg2.connect(**conn_params)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(
                """
                select table_name
                from information_schema.tables
                where table_schema = 'public'
                order by table_name
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def source_id_for_table(host: str, dbname: str, table_name: str) -> str:
    key = f"db:{host}:{dbname}:{table_name}"
    return "db_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def get_foreign_keys(conn_params: dict, table_names: list[str]) -> list[dict]:
    """Declared FOREIGN KEY constraints in the public schema where BOTH the
    referencing and referenced table are in `table_names`.
    """
    conn = _connect(conn_params)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  tc.table_name  as left_table,
                  kcu.column_name as left_column,
                  ccu.table_name  as right_table,
                  ccu.column_name as right_column
                from information_schema.table_constraints tc
                join information_schema.key_column_usage kcu
                  on tc.constraint_name = kcu.constraint_name and tc.table_schema = kcu.table_schema
                join information_schema.constraint_column_usage ccu
                  on ccu.constraint_name = tc.constraint_name and ccu.table_schema = tc.table_schema
                where tc.constraint_type = 'FOREIGN KEY' and tc.table_schema = 'public'
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    names = set(table_names)
    return [
        {"left_table": lt, "left_column": lc, "right_table": rt, "right_column": rc}
        for lt, lc, rt, rc in rows
        if lt in names and rt in names
    ]


def sample_columns(conn_params: dict, table_name: str, columns: list[str], limit: int) -> dict[str, set]:
    """{column_name: set of up to `limit` distinct non-null values}. One
    connection, one query per column — used to compare value overlap across
    tables when hunting for join keys.
    """
    conn = _connect(conn_params)
    out: dict[str, set] = {}
    try:
        with conn.cursor() as cur:
            for col in columns:
                cur.execute(
                    pg_sql.SQL("SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT %s").format(
                        c=pg_sql.Identifier(col), t=pg_sql.Identifier(table_name)
                    ),
                    (limit,),
                )
                out[col] = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    return out


def get_schema(conn_params: dict, table_name: str) -> list[dict]:
    """Column list for one public-schema table, in ordinal order."""
    conn = _connect(conn_params)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select column_name, data_type, is_nullable
                from information_schema.columns
                where table_schema = 'public' and table_name = %s
                order by ordinal_position
                """,
                (table_name,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"name": r[0], "type": r[1], "nullable": r[2] == "YES"} for r in rows]


def sample_rows(conn_params: dict, table_name: str, n: int = 20) -> list[dict]:
    """A handful of real rows, used as few-shot context for SQL generation."""
    conn = _connect(conn_params)
    try:
        query = pg_sql.SQL("SELECT * FROM {} LIMIT %s").format(pg_sql.Identifier(table_name))
        df = pd.read_sql(query.as_string(conn), conn, params=(n,))
    finally:
        conn.close()
    return df.fillna("").astype(str).to_dict(orient="records")


def build_table_card(conn_params: dict, table_name: str, schema: list[dict]) -> tuple[str, int]:
    """Human-readable summary of a table: row count, and per-column stats.
    Returns (card_text, total_row_count).

    For low-cardinality text columns the distinct values are listed in full —
    that alone answers most "which / what values / how many kinds" questions
    when the SQL path can't run.
    """
    conn = _connect(conn_params)
    lines = [f"Table: {table_name}"]
    try:
        with conn.cursor() as cur:
            cur.execute(pg_sql.SQL("SELECT COUNT(*) FROM {}").format(pg_sql.Identifier(table_name)))
            total = cur.fetchone()[0]
            lines.append(f"Rows: {total}")
            lines.append("Columns:")

            for col in schema:
                name, dtype = col["name"], col["type"]
                ident = pg_sql.Identifier(name)
                cur.execute(
                    pg_sql.SQL("SELECT count(*) FILTER (WHERE {c} IS NULL), count(DISTINCT {c}) FROM {t}").format(
                        c=ident, t=pg_sql.Identifier(table_name)
                    )
                )
                nulls, distinct = cur.fetchone()
                detail = f"{distinct} distinct, {nulls} null"

                if dtype in _NUMERIC_TYPES or dtype in _TEMPORAL_TYPES:
                    cur.execute(
                        pg_sql.SQL("SELECT min({c}), max({c}) FROM {t}").format(
                            c=ident, t=pg_sql.Identifier(table_name)
                        )
                    )
                    lo, hi = cur.fetchone()
                    detail += f", range {lo} .. {hi}"
                elif 0 < distinct <= settings.TABLE_CARD_MAX_DISTINCT:
                    cur.execute(
                        pg_sql.SQL("SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL ORDER BY {c}").format(
                            c=ident, t=pg_sql.Identifier(table_name)
                        )
                    )
                    values = ", ".join(str(v[0]) for v in cur.fetchall())
                    if len(values) > settings.TABLE_CARD_VALUES_MAX_CHARS:
                        values = values[: settings.TABLE_CARD_VALUES_MAX_CHARS] + ", …"
                    detail += f", values: [{values}]"

                lines.append(f"  - {name} ({dtype}): {detail}")
    finally:
        conn.close()

    return "\n".join(lines), total


def run_readonly_query(conn_params: dict, query: str) -> tuple[list[str], list[list]]:
    """Execute one already-validated SELECT read-only, with a statement timeout.
    Returns (column_names, rows). Raises psycopg2 errors to the caller.
    """
    conn = _connect(conn_params)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(settings.SQL_STATEMENT_TIMEOUT_MS)}")
            cur.execute(query)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()] if cur.description else []
    finally:
        conn.close()
    return columns, rows


def table_to_chunks(conn_params: dict, table_name: str) -> tuple[list[str], int]:
    conn = psycopg2.connect(**conn_params)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(pg_sql.SQL("SELECT COUNT(*) FROM {}").format(pg_sql.Identifier(table_name)))
            total_rows = cur.fetchone()[0]
        query = pg_sql.SQL("SELECT * FROM {} LIMIT %s").format(pg_sql.Identifier(table_name))
        df = pd.read_sql(query.as_string(conn), conn, params=(settings.DB_TABLE_ROW_LIMIT,))
    finally:
        conn.close()

    df = df.fillna("")
    columns = list(df.columns)
    header = f"Table: {table_name} | Columns: {', '.join(str(c) for c in columns)}"

    chunks = []
    block_lines: list[str] = []
    block_chars = len(header)

    for row_idx, row in df.iterrows():
        pairs = ", ".join(f"{col}={row[col]}" for col in columns)
        line = f"Row {row_idx + 2}: {pairs}"
        if block_lines and (
            len(block_lines) >= settings.ROWS_PER_CHUNK or block_chars + len(line) > settings.CHUNK_CHAR_BUDGET
        ):
            chunks.append(header + "\n" + "\n".join(block_lines))
            block_lines = []
            block_chars = len(header)
        block_lines.append(line)
        block_chars += len(line) + 1

    if block_lines:
        chunks.append(header + "\n" + "\n".join(block_lines))

    return chunks, total_rows
