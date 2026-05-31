from __future__ import annotations

import re
from pathlib import Path

PDF_EXTRACT_MAX_CHARS = 60000
TEXT_DOCUMENT_MAX_CHARS = 60000
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
VIDEO_MIME_PREFIX = "video/"
TEXT_DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".log",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".sh",
}
TEXT_DOCUMENT_MIME_TYPES = {
    "application/json",
    "application/x-ndjson",
    "application/xml",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/tab-separated-values",
    "text/xml",
}


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return cleaned or "document.pdf"


def is_video_file(filename: str, mime_type: str) -> bool:
    if (mime_type or "").lower().startswith(VIDEO_MIME_PREFIX):
        return True
    return Path(filename or "").suffix.lower() in VIDEO_EXTENSIONS


def is_text_document(filename: str, mime_type: str) -> bool:
    lowered_mime = (mime_type or "").lower()
    if lowered_mime.startswith("text/") or lowered_mime in TEXT_DOCUMENT_MIME_TYPES:
        return True
    return Path(filename or "").suffix.lower() in TEXT_DOCUMENT_EXTENSIONS


def extract_pdf_text(pdf_path: Path, max_chars: int = PDF_EXTRACT_MAX_CHARS) -> tuple[str, bool]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is not installed in the Telegram operator environment") from exc

    reader = PdfReader(str(pdf_path))
    parts: list[str] = []
    total_chars = 0
    truncated = False
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        page_block = f"--- Page {page_number} ---\n{page_text}"
        parts.append(page_block)
        total_chars += len(page_block)
        if total_chars >= max_chars:
            truncated = True
            break
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    if truncated:
        text += "\n\n[PDF extraction truncated before sending to the agent.]"
    return text, truncated


def read_text_document(text_path: Path, max_chars: int = TEXT_DOCUMENT_MAX_CHARS) -> tuple[str, bool]:
    raw = text_path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
        text += "\n\n[Text document truncated before sending to the agent.]"
    return text, truncated
