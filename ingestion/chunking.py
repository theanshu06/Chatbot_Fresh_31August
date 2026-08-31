"""Generic character-budget chunker, used by sources that don't have a
natural unit to chunk by (pages for PDF, rows for DB tables — those chunk
themselves in their own source module). Website text is the main user here.
"""

from ingestion.config import settings


def chunk_text(text: str, char_budget: int = settings.CHUNK_CHAR_BUDGET) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    block_lines: list[str] = []
    block_chars = 0

    for para in paragraphs:
        if block_lines and block_chars + len(para) > char_budget:
            chunks.append("\n\n".join(block_lines))
            block_lines = []
            block_chars = 0
        block_lines.append(para)
        block_chars += len(para) + 2

    if block_lines:
        chunks.append("\n\n".join(block_lines))

    return chunks
