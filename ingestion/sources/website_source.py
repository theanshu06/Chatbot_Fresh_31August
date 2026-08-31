"""Website -> text chunks. BFS-crawls same-domain links starting from a URL,
capped by max_pages and max_depth so a crawl can never run away, then chunks
each page's visible text.
"""

import hashlib
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ingestion.chunking import chunk_text
from ingestion.config import settings

SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "svg"}


def source_id_for_domain(domain: str) -> str:
    return "web_" + hashlib.sha256(domain.encode()).hexdigest()[:16]


def _extract_text_and_links(html: str, base_url: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)

    links = [urljoin(base_url, a["href"]).split("#")[0] for a in soup.find_all("a", href=True)]
    return text, links


def crawl_website(start_url: str, max_pages: int, max_depth: int) -> list[dict]:
    max_pages = min(max_pages, settings.CRAWL_MAX_PAGES_LIMIT)
    max_depth = min(max_depth, settings.CRAWL_MAX_DEPTH_LIMIT)
    domain = urlparse(start_url).netloc

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(start_url, 0)]
    pages: list[dict] = []
    headers = {"User-Agent": settings.CRAWL_USER_AGENT}

    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            response = requests.get(url, headers=headers, timeout=settings.CRAWL_REQUEST_TIMEOUT)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", ""):
                continue
        except Exception:
            continue

        text, links = _extract_text_and_links(response.text, url)
        if text.strip():
            pages.append({"url": url, "text": text})

        if depth < max_depth:
            for link in links:
                if urlparse(link).netloc == domain and link not in visited:
                    queue.append((link, depth + 1))

    return pages


def website_to_chunks(start_url: str, max_pages: int, max_depth: int) -> tuple[list[str], int]:
    pages = crawl_website(start_url, max_pages, max_depth)
    chunks = []
    for page in pages:
        for piece in chunk_text(page["text"]):
            chunks.append(f"URL: {page['url']}\n\n{piece}")
    return chunks, len(pages)
