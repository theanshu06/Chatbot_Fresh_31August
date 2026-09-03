"""Discover join relationships between freshly-ingested database tables.

Two sources, run once per database ingest:
  1. Declared foreign keys  -> stored as status "confirmed" (authoritative).
  2. Value-overlap heuristic -> stored as status "suggested". For every pair of
     same-type columns across two tables, sample their distinct values and
     measure containment (|A intersect B| / min(|A|, |B|)). Above
     REL_VALUE_OVERLAP_MIN the columns probably name the same thing
     (e.g. plant_date / reading_date).

     Integer columns are deliberately excluded from the heuristic: small-integer
     ranges overlap by coincidence far too often (an `id` and a `total_manpower`
     both run 1..200). Real integer keys are almost always *declared* foreign
     keys, which path 1 already catches.

Only the user (in the UI) promotes a "suggested" relationship to "confirmed";
only "confirmed" relationships are ever shown to the SQL model.
"""

from ingestion import db_relationships
from ingestion.config import settings
from ingestion.sources import database_source

_KEYISH_FAMILIES = {"text", "date", "time", "timestamp"}


def _family(dtype: str) -> str | None:
    """Coarse type family — two columns can only join if they share one.
    Floating/measurement numerics are deliberately excluded (never join keys).
    """
    d = dtype.lower()
    if d in ("smallint", "integer", "bigint"):
        return "int"
    if d in ("character varying", "character", "text", "varchar", "char", "citext", "uuid"):
        return "text"
    if d == "date":
        return "date"
    if d.startswith("time"):
        return "time"
    if d.startswith("timestamp"):
        return "timestamp"
    return None


def detect(conn_params: dict, tables: list[dict]) -> list[dict]:
    """`tables`: [{"source_id", "label", "schema"}]. Writes the relationships it
    finds to the store and returns them.
    """
    written: list[dict] = []
    by_name = {t["label"]: t for t in tables}

    # 1. declared foreign keys -> confirmed
    try:
        fks = database_source.get_foreign_keys(conn_params, list(by_name))
    except Exception:
        fks = []
    fk_pairs: dict = {}
    for fk in fks:
        fk_pairs.setdefault((fk["left_table"], fk["right_table"]), []).append(
            {"left": fk["left_column"], "right": fk["right_column"]}
        )
    for (lt, rt), joins in fk_pairs.items():
        written.append(
            db_relationships.upsert(
                {
                    "left_table": lt,
                    "right_table": rt,
                    "left_source_id": by_name[lt]["source_id"],
                    "right_source_id": by_name[rt]["source_id"],
                    "joins": joins,
                    "source": "foreign_key",
                    "status": "confirmed",
                    "note": "declared foreign key",
                }
            )
        )

    # 2. value-overlap heuristic -> suggested
    fam: dict = {}
    samples: dict = {}
    for t in tables:
        keyish = [c["name"] for c in t["schema"] if _family(c["type"]) in _KEYISH_FAMILIES]
        for c in t["schema"]:
            fam[(t["source_id"], c["name"])] = _family(c["type"])
        try:
            samples[t["source_id"]] = database_source.sample_columns(
                conn_params, t["label"], keyish, settings.REL_SAMPLE_VALUES
            )
        except Exception:
            samples[t["source_id"]] = {}

    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            a, b = tables[i], tables[j]
            joins = []
            for ca, va in samples.get(a["source_id"], {}).items():
                if not va:
                    continue
                for cb, vb in samples.get(b["source_id"], {}).items():
                    if not vb or fam.get((a["source_id"], ca)) != fam.get((b["source_id"], cb)):
                        continue
                    inter = len(va & vb)
                    if not inter:
                        continue
                    containment = inter / min(len(va), len(vb))
                    if containment >= settings.REL_VALUE_OVERLAP_MIN:
                        joins.append({"left": ca, "right": cb, "overlap": round(containment, 2)})
            if joins:
                note = "matched on value overlap: " + ", ".join(
                    f"{j['left']}~{j['right']} ({int(j['overlap'] * 100)}%)" for j in joins
                )
                written.append(
                    db_relationships.upsert(
                        {
                            "left_table": a["label"],
                            "right_table": b["label"],
                            "left_source_id": a["source_id"],
                            "right_source_id": b["source_id"],
                            "joins": [{"left": j["left"], "right": j["right"]} for j in joins],
                            "source": "heuristic",
                            "status": "suggested",
                            "note": note,
                        }
                    )
                )

    return written
