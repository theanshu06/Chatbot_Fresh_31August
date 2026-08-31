"""
Ingestion Pipeline API
----------------------
Unifies three data sources into one searchable vector store:

    Data Sources
         |
    -----+-----+-----
    |          |     |
   PDF     Website  Database
    |          |     |
    -----+-----+-----
         |
   Ingestion Pipeline   (extract -> chunk -> embed -> store, this app)
         |
      ChromaDB           (local vector store, persisted to disk)

Endpoints:
    POST /api/ingest/pdf        multipart file upload
    POST /api/ingest/website    {"url": ..., "max_pages": ..., "max_depth": ...}
    POST /api/ingest/database   {"host": ..., "dbname": ..., "user": ..., "password": ..., "tables": [...]}
    POST /api/search            {"query": ...}  -- verification/testing only
    GET  /api/health

Run with:
    uvicorn app:app --reload --port 8100

Answering:
    - PDF / website questions      -> RAG over retrieved chunks (CHAT_MODEL)
    - Database questions           -> text-to-SQL against the live DB (SQL_MODEL),
                                      falling back to a stored "table card" when
                                      SQL generation fails or the DB is unreachable

Prerequisites (one-time):
    1. Install Ollama: https://ollama.com/download
    2. ollama pull nomic-embed-text
    3. ollama pull llama3.2:3b
    4. ollama pull qwen2.5-coder:3b
    5. pip install -r requirements.txt
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ingestion.routes import router

app = FastAPI(title="Ingestion Pipeline API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
