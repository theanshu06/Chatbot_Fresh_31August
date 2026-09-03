"""Central settings for the ingestion pipeline. All overridable via .env."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Embeddings (Ollama, local — same model already used by chatbot-backend)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    EMBED_MODEL: str = "nomic-embed-text"
    EMBED_NUM_CTX: int = 4096
    # Cheap guard so we never POST a huge payload to Ollama; the precise
    # token-level truncation is done by Ollama (embed(truncate=True)).
    EMBED_MAX_CHARS: int = 8000

    # Chat / answer generation (Ollama, local)
    CHAT_MODEL: str = "llama3.2:3b"
    CHAT_NUM_CTX: int = 8192
    CHAT_TEMPERATURE: float = 0.0
    CHAT_MAX_TOKENS: int = 400  # num_predict cap on the answer
    CHAT_KEEP_ALIVE: str = "30m"  # how long Ollama keeps the model in memory
    CHAT_TOP_K_DEFAULT: int = 5  # how many chunks to retrieve as context

    # Database question answering — text-to-SQL path (Ollama, local).
    # A code-tuned model generates the SQL AND phrases the final answer for the
    # database path; CHAT_MODEL stays on the PDF/website RAG path.
    SQL_MODEL: str = "qwen2.5-coder:3b"
    SQL_NUM_CTX: int = 8192
    SQL_MAX_RETRIES: int = 2  # re-prompt with the DB error this many times
    SQL_ROW_LIMIT: int = 200  # hard LIMIT forced onto every generated query
    SQL_STATEMENT_TIMEOUT_MS: int = 5000  # Postgres statement_timeout per query
    SQL_RESULT_CHARS_MAX: int = 6000  # cap on result text fed back for phrasing
    SQL_MAX_TABLES: int = 6  # table cards offered to the SQL model at once

    # Table card (Option C fallback): distinct values are listed for a text
    # column only when it has at most this many, and the rendered value list for
    # one column is truncated past this many characters (keeps wide-table cards
    # under the embedder's limit and the SQL prompt small).
    TABLE_CARD_MAX_DISTINCT: int = 50
    TABLE_CARD_VALUES_MAX_CHARS: int = 600

    # Vector store
    # Everything the app persists lives under here. Point it at a drive with
    # free space (e.g. CHROMA_DIR=D:/chatbot_fresh_store) — a full disk corrupts
    # the vector store.
    CHROMA_DIR: Path = Path.home() / ".chatbot_fresh_store"
    COLLECTION_NAME: str = "ingested_documents"

    # Cached Postgres connection params (so text-to-SQL can reconnect at question
    # time) and table relationships. Both default to a file under CHROMA_DIR; set
    # explicitly to override. DB_CONN_FILE holds credentials in PLAINTEXT — this
    # is a local single-user tool; delete the file to revoke.
    DB_CONN_FILE: Path | None = None
    DB_REL_FILE: Path | None = None

    REL_VALUE_OVERLAP_MIN: float = 0.8  # value-containment needed to suggest a join
    REL_SAMPLE_VALUES: int = 500  # distinct values sampled per column for overlap
    REL_MIN_DISTINCT: int = 2  # skip literal-constant columns; the keyish check
    #                            below is what actually filters category columns
    REL_KEY_UNIQUENESS: float = 0.9  # a join needs at least one side to look like a
    #                                  key: distinct/rows >= this. Kills category-
    #                                  vs-category matches (status~status, etc.)

    # Chunking
    CHUNK_CHAR_BUDGET: int = 3000
    PAGES_PER_CHUNK: int = 2  # PDF
    ROWS_PER_CHUNK: int = 20  # Database

    # Database ingestion speed vs. fallback richness.
    # The text-to-SQL path answers from a live query and NEVER reads embedded
    # rows — it only needs the schema, the table card, and the sample rows. So by
    # default we skip embedding row chunks entirely: ingest drops from minutes to
    # seconds. Set EMBED_DB_ROWS=true to also embed rows (used only by the RAG
    # fallback when SQL generation fails, and by the "Show ingested" view).
    EMBED_DB_ROWS: bool = False
    DB_SAMPLE_ROWS: int = 20  # real rows stored per table as few-shot context

    # Website crawl limits — a crawl with no cap could wander an entire site
    # indefinitely, so both a page count and a depth ceiling are mandatory.
    CRAWL_MAX_PAGES_DEFAULT: int = 20
    CRAWL_MAX_PAGES_LIMIT: int = 100
    CRAWL_MAX_DEPTH_DEFAULT: int = 2
    CRAWL_MAX_DEPTH_LIMIT: int = 5
    CRAWL_REQUEST_TIMEOUT: int = 10
    CRAWL_USER_AGENT: str = "ChatbotFreshIngestionBot/1.0"

    # Database source
    DB_TABLE_ROW_LIMIT: int = 5000

    class Config:
        env_file = ".env"

    def model_post_init(self, __context) -> None:
        # Default the sidecar files to live alongside the vector store.
        if self.DB_CONN_FILE is None:
            self.DB_CONN_FILE = self.CHROMA_DIR / "db_connections.json"
        if self.DB_REL_FILE is None:
            self.DB_REL_FILE = self.CHROMA_DIR / "db_relationships.json"


settings = Settings()
