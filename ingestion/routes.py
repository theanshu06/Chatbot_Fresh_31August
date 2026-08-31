from fastapi import APIRouter, File, HTTPException, UploadFile

from ingestion import chat, pipeline, vector_store
from ingestion.models import (
    ChatRequest,
    DatabaseConnectRequest,
    DatabaseIngestRequest,
    SearchRequest,
    WebsiteIngestRequest,
)
from ingestion.sources import database_source

router = APIRouter()


@router.get("/api/health")
def health():
    return {"ok": True}


@router.post("/api/ingest/pdf")
async def ingest_pdf(file: UploadFile = File(...)):
    data = await file.read()
    try:
        return pipeline.ingest_pdf(data, file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to ingest PDF: {e}")


@router.post("/api/ingest/website")
def ingest_website(req: WebsiteIngestRequest):
    try:
        return pipeline.ingest_website(req.url, req.max_pages, req.max_depth)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to ingest website: {e}")


@router.post("/api/database/tables")
def list_database_tables(req: DatabaseConnectRequest):
    """Connects and lists tables only — no ingestion. Lets the UI show a
    checklist of real tables instead of asking the user to type names blind.
    """
    conn_params = dict(host=req.host, port=req.port, dbname=req.dbname, user=req.user, password=req.password)
    try:
        tables = database_source.list_table_names(conn_params)
    except Exception as e:
        raise HTTPException(400, f"Couldn't connect: {e}")
    return {"tables": tables}


@router.post("/api/ingest/database")
def ingest_database(req: DatabaseIngestRequest):
    conn_params = dict(host=req.host, port=req.port, dbname=req.dbname, user=req.user, password=req.password)
    try:
        return pipeline.ingest_database(conn_params, req.tables)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Couldn't ingest database: {e}")


@router.post("/api/search")
def search(req: SearchRequest):
    """Verification endpoint — not the chatbot itself, just proof that
    ingested chunks are retrievable by semantic similarity.
    """
    return {"results": vector_store.search(req.query, top_k=req.top_k, source_type=req.source_type)}


@router.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """The chatbot: retrieve context chunks, then answer with the local LLM.
    Returns the answer plus a full `trace` of every input to the model.
    """
    if not req.question.strip():
        raise HTTPException(400, "question is empty")
    try:
        return chat.answer(req.question, top_k=req.top_k, source_type=req.source_type)
    except Exception as e:
        raise HTTPException(500, f"Chat failed: {e}")


@router.get("/api/ingested")
def ingested():
    """The "Show" data: every source ingested so far, with every chunk of
    text that was actually extracted and embedded from it.
    """
    return {"sources": vector_store.list_sources()}


@router.delete("/api/ingested")
def clear_ingested():
    """Wipe every ingested source from the vector store."""
    removed = vector_store.clear_all()
    return {"removed_chunks": removed}


@router.delete("/api/ingested/{source_id}")
def delete_ingested_source(source_id: str):
    """Remove a single ingested source (all of its chunks)."""
    removed = vector_store.delete_source(source_id)
    if removed == 0:
        raise HTTPException(404, f"No ingested source with id {source_id!r}")
    return {"removed_chunks": removed}
