"""Central settings for the ingestion pipeline. All overridable via .env."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Embeddings (Ollama, local — same model already used by chatbot-backend)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    EMBED_MODEL: str = "nomic-embed-text"
    EMBED_NUM_CTX: int = 4096

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

    # Table card (Option C fallback): distinct values are listed for a text
    # column only when it has at most this many.
    TABLE_CARD_MAX_DISTINCT: int = 50

    # Vector store
    CHROMA_DIR: Path = Path.home() / ".chatbot_fresh_store"
    COLLECTION_NAME: str = "ingested_documents"

    # Where per-table Postgres connection params are cached so the text-to-SQL
    # path can reconnect at question time. Holds credentials in PLAINTEXT — this
    # is a local single-user tool; delete the file to revoke.
    DB_CONN_FILE: Path = Path.home() / ".chatbot_fresh_store" / "db_connections.json"

    # Chunking
    CHUNK_CHAR_BUDGET: int = 3000
    PAGES_PER_CHUNK: int = 2  # PDF
    ROWS_PER_CHUNK: int = 20  # Database

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


settings = Settings()
