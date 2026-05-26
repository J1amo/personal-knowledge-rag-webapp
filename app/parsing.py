from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from .text_utils import normalize_text, sha256_text


@dataclass
class ParsedPage:
    page_number: int
    text: str
    section_title: str | None = None
    image_count: int = 0
    captions: list[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    parser_name: str
    parser_version: str
    content_hash: str
    language_sample: str
    pages: list[ParsedPage]
    metadata: dict[str, Any] = field(default_factory=dict)


CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.|Table)\s+\d+[:.\s].{8,}", re.IGNORECASE)
SECTION_RE = re.compile(
    r"^\s*(abstract|introduction|background|method|methods|experiments?|results?|discussion|conclusion|references)\b",
    re.IGNORECASE,
)


def _first_section_title(text: str) -> str | None:
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if SECTION_RE.search(clean):
            return clean[:160]
        if len(clean) <= 100 and clean.isupper() and any(ch.isalpha() for ch in clean):
            return clean[:160]
    return None


def _captions(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        clean = line.strip()
        if CAPTION_RE.match(clean):
            hits.append(clean[:500])
    return hits


def parse_pdf(path: Path) -> ParsedDocument:
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("PyMuPDF/fitz is required for PDF parsing in this MVP") from exc

    pages: list[ParsedPage] = []
    metadata: dict[str, Any] = {}
    with fitz.open(path) as doc:
        metadata = {k: v for k, v in (doc.metadata or {}).items() if v}
        for idx, page in enumerate(doc, start=1):
            raw_text = page.get_text("text") or ""
            text = normalize_text(raw_text)
            image_count = len(page.get_images(full=True))
            pages.append(
                ParsedPage(
                    page_number=idx,
                    text=text,
                    section_title=_first_section_title(text),
                    image_count=image_count,
                    captions=_captions(text),
                )
            )

    combined_text = "\n\n".join(page.text for page in pages if page.text)
    return ParsedDocument(
        parser_name="pymupdf",
        parser_version=config.PARSER_VERSION,
        content_hash=sha256_text(normalize_text(combined_text)),
        language_sample=combined_text[:4000],
        pages=pages,
        metadata=metadata,
    )


def chunk_page_text(text: str, *, max_chars: int = 2200, overlap_chars: int = 240) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            for start in range(0, len(paragraph), max_chars - overlap_chars):
                piece = paragraph[start : start + max_chars].strip()
                if piece:
                    chunks.append(piece)
            continue
        if not current:
            current = paragraph
        elif len(current) + 2 + len(paragraph) <= max_chars:
            current += "\n\n" + paragraph
        else:
            chunks.append(current.strip())
            prefix = current[-overlap_chars:].strip() if overlap_chars and current else ""
            current = (prefix + "\n\n" + paragraph).strip() if prefix else paragraph
    if current:
        chunks.append(current.strip())
    return chunks
