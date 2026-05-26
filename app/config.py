from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_MODELS_DIR = PROJECT_ROOT / "local_models"


def load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env()

DATA_DIR = Path(os.getenv("PKB_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = Path(os.getenv("PKB_RAW_DIR", DATA_DIR / "raw"))
DB_DIR = Path(os.getenv("PKB_DB_DIR", PROJECT_ROOT / "db"))
INDEX_DIR = Path(os.getenv("PKB_INDEX_DIR", PROJECT_ROOT / "indexes"))
CACHE_DIR = Path(os.getenv("PKB_CACHE_DIR", PROJECT_ROOT / "cache"))
BACKUP_DIR = Path(os.getenv("PKB_BACKUP_DIR", PROJECT_ROOT / "backups"))
OUTPUT_DIR = Path(os.getenv("PKB_OUTPUT_DIR", PROJECT_ROOT / "outputs"))
LOCAL_MODELS_DIR = Path(os.getenv("LOCAL_MODELS_DIR", DEFAULT_LOCAL_MODELS_DIR))

DB_PATH = Path(os.getenv("PKB_DB_PATH", DB_DIR / "knowledge.sqlite"))

PARSER_VERSION = "pymupdf_pdf_parser_v1"
INGESTION_VERSION = "canonical_ingestion_v1"
CHUNKING_VERSION = "page_aware_text_chunks_v1"

LOCAL_VECTOR_INDEX = "local_hash_embedding_v1"
LOCAL_VECTOR_MODEL = "local_hash_embedding_384d"
API_VECTOR_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
API_VECTOR_INDEX = "api_" + API_VECTOR_MODEL.replace("-", "_") + "_v1"
BM25_INDEX = "bm25_v1"
GRAPH_INDEX = "graph_entities_v1"

INDEX_DEFINITIONS = {
    LOCAL_VECTOR_INDEX: {
        "backend_type": "local",
        "model_name": LOCAL_VECTOR_MODEL,
        "index_version": "1",
    },
    API_VECTOR_INDEX: {
        "backend_type": "api",
        "model_name": API_VECTOR_MODEL,
        "index_version": "1",
    },
    BM25_INDEX: {
        "backend_type": "bm25",
        "model_name": "okapi_bm25",
        "index_version": "1",
    },
    GRAPH_INDEX: {
        "backend_type": "graph",
        "model_name": "regex_entity_graph",
        "index_version": "1",
    },
}

DOMAIN_RAW_FOLDERS = {
    "paper": "papers",
    "chat": "chats",
    "image": "images",
    "doc": "docs",
    "project": "docs",
    "note": "notes",
    "misc": "misc",
}

PUBLIC_SENSITIVITIES = {"public"}
PRIVATE_SENSITIVITIES = {"private", "confidential"}


def ensure_runtime_dirs() -> None:
    for path in (
        DATA_DIR,
        RAW_DIR / "papers",
        RAW_DIR / "chats",
        RAW_DIR / "images",
        RAW_DIR / "docs",
        RAW_DIR / "notes",
        RAW_DIR / "misc",
        DB_DIR,
        INDEX_DIR / "local_vector",
        INDEX_DIR / "api_vector",
        INDEX_DIR / "bm25",
        INDEX_DIR / "graph",
        CACHE_DIR,
        BACKUP_DIR,
        OUTPUT_DIR,
        LOCAL_MODELS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
