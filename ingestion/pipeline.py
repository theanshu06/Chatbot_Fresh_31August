"""Orchestrator: wires each source module to the vector store. This is the
single place that knows "extract -> chunk -> embed -> store" for every
source type — routes.py just calls into here.
"""

import json
from urllib.parse import urlparse

from ingestion import db_connections, relationship_detect, vector_store
from ingestion.sources import database_source, pdf_source, website_source


def ingest_pdf(data: bytes, filename: str) -> dict:
    chunks = pdf_source.pdf_to_chunks(data)
    if not chunks:
        raise ValueError(
            "No extractable text found in this PDF. It may be a scanned/image-only "
            "document (no text layer)."
        )
    source_id = "pdf_" + pdf_source.file_hash(data)
    count = vector_store.store_chunks("pdf", source_id, filename, chunks)
    return {"source_id": source_id, "filename": filename, "chunks": count}


def ingest_website(start_url: str, max_pages: int, max_depth: int) -> dict:
    chunks, pages_crawled = website_source.website_to_chunks(start_url, max_pages, max_depth)
    if not chunks:
        raise ValueError("No extractable text found while crawling this site.")
    domain = urlparse(start_url).netloc
    source_id = website_source.source_id_for_domain(domain)
    count = vector_store.store_chunks("website", source_id, domain, chunks)
    return {"source_id": source_id, "domain": domain, "pages_crawled": pages_crawled, "chunks": count}


def ingest_database(conn_params: dict, tables: list[str] | None) -> dict:
    target_tables = tables or database_source.list_table_names(conn_params)
    if not target_tables:
        raise ValueError("No tables found to ingest.")

    ingested = []
    total_chunks = 0
    schemas = []  # for relationship detection after every table is stored
    for table_name in target_tables:
        chunks, total_rows = database_source.table_to_chunks(conn_params, table_name)
        if not chunks:
            continue
        source_id = database_source.source_id_for_table(conn_params["host"], conn_params["dbname"], table_name)

        # Row chunks (kept for transparency + the RAG fallback).
        count = vector_store.store_chunks(
            "database", source_id, table_name, chunks, kind="row_chunk", replace=True
        )

        # Table card + schema (Option C fallback and the text-to-SQL prompt).
        schema = database_source.get_schema(conn_params, table_name)
        card = database_source.build_table_card(conn_params, table_name, schema)
        samples = database_source.sample_rows(conn_params, table_name)
        vector_store.store_chunks(
            "database",
            source_id,
            table_name,
            [card],
            kind="table_card",
            extra_metadata={
                "schema_json": json.dumps(schema),
                "sample_rows_json": json.dumps(samples, default=str),
                "row_count": int(total_rows),
            },
            replace=False,
            id_suffix="card",
        )

        # Cache the connection so the text-to-SQL path can reconnect at question time.
        db_connections.save(source_id, conn_params)

        schemas.append({"source_id": source_id, "label": table_name, "schema": schema})
        total_chunks += count + 1
        ingested.append({"table": table_name, "chunks": count, "total_rows": total_rows})

    if not ingested:
        raise ValueError("None of the requested tables had any rows to ingest.")

    # Detect join relationships across every table in THIS database ingested so
    # far (not just this batch), so a table added later links to earlier ones.
    known = {s["source_id"] for s in schemas}
    for t in vector_store.get_database_tables():
        if t["source_id"] in known:
            continue
        prior = db_connections.get(t["source_id"]) or {}
        if prior.get("host") == conn_params.get("host") and prior.get("dbname") == conn_params.get("dbname"):
            schemas.append({"source_id": t["source_id"], "label": t["label"], "schema": t["schema"]})
    try:
        relationships = relationship_detect.detect(conn_params, schemas)
    except Exception:
        relationships = []

    return {
        "tables_ingested": ingested,
        "total_chunks": total_chunks,
        "relationships": relationships,
    }
