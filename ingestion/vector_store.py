"""Vector store: one Chroma collection holds chunks from ALL sources (PDF,
website, database), each tagged with metadata (source_type, source_id, label)
so results can be filtered or traced back to where they came from.
"""

import json

import chromadb
import ollama

from ingestion import db_connections, db_relationships
from ingestion.config import settings

chroma_client = chromadb.PersistentClient(path=str(settings.CHROMA_DIR))


def get_collection():
    existing = [c.name for c in chroma_client.list_collections()]
    if settings.COLLECTION_NAME in existing:
        return chroma_client.get_collection(settings.COLLECTION_NAME)
    return chroma_client.create_collection(settings.COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def embed(text: str) -> list[float]:
    # nomic-embed-text hard-caps input at ~2048 tokens and errors (not truncates)
    # if a chunk is longer — which happens for wide table cards that list every
    # distinct value of many columns. Truncating here keeps the chunk findable
    # (the full text is still stored as the document and shown to the SQL model).
    response = ollama.embeddings(
        model=settings.EMBED_MODEL,
        prompt=text[: settings.EMBED_MAX_CHARS],
        options={"num_ctx": settings.EMBED_NUM_CTX},
    )
    return response["embedding"]


def store_chunks(
    source_type: str,
    source_id: str,
    label: str,
    chunks: list[str],
    *,
    kind: str = "chunk",
    extra_metadata: dict | None = None,
    replace: bool = True,
    id_suffix: str = "",
) -> int:
    """Embed and store chunks under a deterministic id prefix, so re-ingesting
    the same source (same source_id) overwrites its old chunks instead of
    duplicating them.

    kind           -- tags every chunk ("chunk", "row_chunk", "table_card"),
                      so the chat router and the "Show" view can tell them apart.
    extra_metadata -- merged into each chunk's metadata (e.g. a table card's
                      schema_json / row_count).
    replace        -- delete existing chunks for this source_id first. Set False
                      for a second call that adds to the same source.
    id_suffix      -- distinguishes id namespaces within one source_id, so the
                      row chunks (no suffix) and the card ("card") don't collide.
    """
    if not chunks:
        return 0

    collection = get_collection()
    if replace:
        collection.delete(where={"source_id": source_id})  # clear any previous ingestion of this exact source

    embeddings = [embed(chunk) for chunk in chunks]
    ids = [f"{source_id}_{id_suffix}{i}" for i in range(len(chunks))]
    base = {"source_type": source_type, "source_id": source_id, "label": label, "kind": kind}
    if extra_metadata:
        base = {**base, **extra_metadata}
    metadatas = [dict(base) for _ in chunks]

    collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    return len(chunks)


def get_database_tables() -> list[dict]:
    """Every ingested database table, with its stored card + schema. Used by the
    text-to-SQL path to build the prompt (and to offer join context).
    """
    collection = get_collection()
    if collection.count() == 0:
        return []

    data = collection.get(
        where={"$and": [{"source_type": "database"}, {"kind": "table_card"}]},
        include=["documents", "metadatas"],
    )
    tables = []
    for doc, meta in zip(data["documents"], data["metadatas"]):
        tables.append(
            {
                "source_id": meta["source_id"],
                "label": meta["label"],
                "card": doc,
                "schema": json.loads(meta.get("schema_json", "[]")),
                "sample_rows": json.loads(meta.get("sample_rows_json", "[]")),
                "row_count": meta.get("row_count"),
            }
        )
    return tables


def search(query: str, top_k: int = 5, source_type: str | None = None) -> list[dict]:
    collection = get_collection()
    if collection.count() == 0:
        return []

    query_embedding = embed(query)
    where = {"source_type": source_type} if source_type else None
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        where=where,
        include=["documents", "distances", "metadatas"],
    )

    return [
        {"text": doc, "distance": dist, **meta}
        for doc, dist, meta in zip(results["documents"][0], results["distances"][0], results["metadatas"][0])
    ]


def delete_source(source_id: str) -> int:
    """Remove every chunk belonging to one ingested source. Returns how many
    chunks were deleted.
    """
    collection = get_collection()
    before = collection.count()
    collection.delete(where={"source_id": source_id})
    db_connections.forget(source_id)  # drop any cached Postgres credentials
    db_relationships.forget_source(source_id)  # drop joins that referenced this table
    return before - collection.count()


def clear_all() -> int:
    """Wipe the entire collection. Returns how many chunks were removed."""
    collection = get_collection()
    removed = collection.count()
    chroma_client.delete_collection(settings.COLLECTION_NAME)
    get_collection()  # recreate an empty collection so later calls still work
    settings.DB_CONN_FILE.unlink(missing_ok=True)  # forget all cached DB credentials
    settings.DB_REL_FILE.unlink(missing_ok=True)  # forget all table relationships
    return removed


def list_sources() -> list[dict]:
    """Every chunk currently stored, grouped by source — the "Show" button's
    data. Each chunk's text IS what got embedded, so this doubles as both
    "what did you extract from my data" and "what did you actually embed".
    """
    collection = get_collection()
    if collection.count() == 0:
        return []

    data = collection.get(include=["documents", "metadatas"])
    grouped: dict[str, dict] = {}
    for chunk_id, doc, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        source_id = meta["source_id"]
        if source_id not in grouped:
            grouped[source_id] = {
                "source_id": source_id,
                "source_type": meta["source_type"],
                "label": meta["label"],
                "chunks": [],
            }
        grouped[source_id]["chunks"].append(
            {"id": chunk_id, "text": doc, "kind": meta.get("kind", "chunk")}
        )

    for source in grouped.values():
        source["chunk_count"] = len(source["chunks"])

    return sorted(grouped.values(), key=lambda s: (s["source_type"], s["label"]))
