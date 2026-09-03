"""Store of join relationships between ingested database tables.

The text-to-SQL path (`sql_answer.py`) can only reliably JOIN two tables if it
knows which columns connect them. Declared Postgres foreign keys give this for
free; when they're absent (common in analytics / imported data) a relationship
is either guessed by a value-overlap heuristic at ingest, or declared by the
user in the UI.

Each relationship:
    {
      "id": "rel_<hash>",
      "left_table": "plant_logs", "right_table": "compressor_readings",
      "left_source_id": "db_...", "right_source_id": "db_...",
      "joins": [{"left": "plant_date", "right": "reading_date"}, ...],
      "source": "foreign_key" | "heuristic" | "user",
      "status": "confirmed" | "suggested",
      "note": "..."
    }

Only `status == "confirmed"` relationships are ever shown to the SQL model.
File: settings.DB_REL_FILE (JSON), a sibling of db_connections.json.
"""

import hashlib
import json

from ingestion.config import settings


def _load() -> list[dict]:
    path = settings.DB_REL_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(rels: list[dict]) -> None:
    path = settings.DB_REL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rels, indent=2), encoding="utf-8")


def _canonical(rel: dict) -> dict:
    """Orient so left_table <= right_table alphabetically, keeping joins aligned,
    so the same relationship declared either way round has one identity.
    """
    if rel["left_table"] <= rel["right_table"]:
        return dict(rel)
    flipped = dict(rel)
    flipped["left_table"], flipped["right_table"] = rel["right_table"], rel["left_table"]
    flipped["left_source_id"], flipped["right_source_id"] = rel["right_source_id"], rel["left_source_id"]
    flipped["joins"] = [{"left": j["right"], "right": j["left"]} for j in rel["joins"]]
    return flipped


def _rel_id(rel: dict) -> str:
    pairs = sorted((j["left"], j["right"]) for j in rel["joins"])
    key = f"{rel['left_source_id']}|{rel['right_source_id']}|{pairs}"
    return "rel_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def list_all() -> list[dict]:
    return _load()


def upsert(rel: dict) -> dict:
    """Insert or update. A confirmed relationship is never downgraded to
    suggested by a later heuristic re-detection.
    """
    rel = _canonical(rel)
    rel["id"] = _rel_id(rel)
    rels = _load()
    for i, existing in enumerate(rels):
        if existing["id"] == rel["id"]:
            if existing.get("status") == "confirmed" and rel.get("status") != "confirmed":
                return existing
            rels[i] = {**existing, **rel}
            _save(rels)
            return rels[i]
    rels.append(rel)
    _save(rels)
    return rel


def confirm(rel_id: str) -> dict | None:
    rels = _load()
    for i, r in enumerate(rels):
        if r["id"] == rel_id:
            rels[i] = {**r, "status": "confirmed"}
            _save(rels)
            return rels[i]
    return None


def delete(rel_id: str) -> bool:
    rels = _load()
    kept = [r for r in rels if r["id"] != rel_id]
    if len(kept) == len(rels):
        return False
    _save(kept)
    return True


def forget_source(source_id: str) -> None:
    """Drop every relationship that touches a now-deleted table."""
    rels = _load()
    kept = [r for r in rels if source_id not in (r["left_source_id"], r["right_source_id"])]
    if len(kept) != len(rels):
        _save(kept)


def for_source_ids(source_ids: set[str], only_confirmed: bool = False) -> list[dict]:
    """Relationships whose BOTH endpoints are in source_ids."""
    out = []
    for r in _load():
        if r["left_source_id"] in source_ids and r["right_source_id"] in source_ids:
            if only_confirmed and r.get("status") != "confirmed":
                continue
            out.append(r)
    return out
