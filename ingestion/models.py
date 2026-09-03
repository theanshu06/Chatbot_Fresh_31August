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


class RelationshipJoin(BaseModel):
    left: str   # column on the left table
    right: str  # column on the right table


class RelationshipRequest(BaseModel):
    """A user-declared join between two already-ingested database tables."""
    left_source_id: str
    right_source_id: str
    left_table: str
    right_table: str
    joins: list[RelationshipJoin] = Field(min_length=1)
    note: str = ""
