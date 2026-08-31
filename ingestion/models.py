from pydantic import BaseModel, Field


class WebsiteIngestRequest(BaseModel):
    url: str
    max_pages: int = Field(default=20, ge=1, le=100)
    max_depth: int = Field(default=2, ge=0, le=5)


class DatabaseConnectRequest(BaseModel):
    host: str
    port: str = "5432"
    dbname: str
    user: str
    password: str


class DatabaseIngestRequest(BaseModel):
    host: str
    port: str = "5432"
    dbname: str
    user: str
    password: str
    tables: list[str] | None = None  # omit to ingest every table in the public schema


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    source_type: str | None = None  # "pdf" | "website" | "database" | None (all)


class ChatRequest(BaseModel):
    question: str
    top_k: int = Field(default=5, ge=1, le=20)  # chunks retrieved as context
    source_type: str | None = None  # "pdf" | "website" | "database" | None (all)
