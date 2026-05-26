from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from pathlib import Path


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
ENTITY_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9\-]{2,}|[A-Z]{2,}(?:-[A-Z0-9]+)*)\b|[\u4e00-\u9fff]{2,}"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(normalize_text(text))]


def extract_entities(text: str, limit: int = 40) -> list[tuple[str, int]]:
    counts = Counter(m.group(0).strip() for m in ENTITY_RE.finditer(text))
    return counts.most_common(limit)


def safe_filename(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name).strip("._")
    return clean or "source"


def detect_language(text: str) -> str:
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if zh > latin:
        return "zh"
    if latin:
        return "en"
    return "unknown"
