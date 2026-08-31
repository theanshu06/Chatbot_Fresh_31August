"""PDF -> text chunks, grouped a few pages at a time so each chunk stays
small enough for the embedding model's context window while still keeping
nearby pages together for coherence.
"""

import hashlib
import io

from pypdf import PdfReader

from ingestion.config import settings


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def pdf_to_chunks(data: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]

    chunks = []
    for start in range(0, len(pages), settings.PAGES_PER_CHUNK):
        block = pages[start : start + settings.PAGES_PER_CHUNK]
        lines = [f"Page {num}:\n{text}" for num, text in block if text.strip()]
        if lines:
            chunks.append("\n\n".join(lines))
    return chunks
