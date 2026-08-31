"""Cache of Postgres connection params, keyed by the ingested source_id.

The text-to-SQL path (see sql_answer.py) needs a live connection when a
question is asked, not just at ingest time. We persist the exact params the
user already typed into the UI so chat "just works" afterwards.

SECURITY: this file (settings.DB_CONN_FILE) stores the password in plaintext.
This is a local, single-user developer tool. Deleting the file revokes access;
the database path then falls back to the table card (Option C).
"""

import json
import os

from ingestion.config import settings

# psycopg2.connect kwargs we persist. Note "password" is included verbatim.
_FIELDS = ("host", "port", "dbname", "user", "password")


def _load() -> dict:
    path = settings.DB_CONN_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    path = settings.DB_CONN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # best effort; a no-op on some Windows setups
    except OSError:
        pass


def save(source_id: str, conn_params: dict) -> None:
    """Record (or overwrite) the connection params for one ingested table."""
    data = _load()
    data[source_id] = {k: conn_params[k] for k in _FIELDS if k in conn_params}
    _save(data)


def get(source_id: str) -> dict | None:
    """Connection params for one ingested table, or None if we have none."""
    return _load().get(source_id)


def forget(source_id: str) -> None:
    """Drop the stored params for one table (called when its source is deleted)."""
    data = _load()
    if data.pop(source_id, None) is not None:
        _save(data)
