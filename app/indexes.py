from __future__ import annotations

import json
import math
import os
import sqlite3
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import config
from .db import connect, init_db, upsert_coverage, utc_now
from .text_utils import extract_entities, sha256_text, tokenize

VECTOR_DIMENSION = 384


def _index_db_path(index_name: str) -> Path:
    if index_name == config.LOCAL_VECTOR_INDEX:
        return config.INDEX_DIR / "local_vector" / f"{index_name}.sqlite"
    if index_name == config.API_VECTOR_INDEX:
        return config.INDEX_DIR / "api_vector" / f"{index_name}.sqlite"
    if index_name == config.BM25_INDEX:
        return config.INDEX_DIR / "bm25" / f"{index_name}.sqlite"
    if index_name == config.GRAPH_INDEX:
        return config.INDEX_DIR / "graph" / f"{index_name}.sqlite"
    raise ValueError(f"Unknown index: {index_name}")


def _index_connect(index_name: str) -> sqlite3.Connection:
    config.ensure_runtime_dirs()
    con = sqlite3.connect(_index_db_path(index_name))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    return con


def _init_vector_index(index_name: str) -> None:
    with _index_connect(index_name) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
              chunk_id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              text_hash TEXT NOT NULL,
              vector_json TEXT NOT NULL,
              dimension INTEGER NOT NULL,
              indexed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_embeddings_source ON embeddings(source_id);
            """
        )


def _init_bm25_index() -> None:
    with _index_connect(config.BM25_INDEX) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS docs (
              chunk_id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              text_hash TEXT NOT NULL,
              tokens_json TEXT NOT NULL,
              length INTEGER NOT NULL,
              indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stats (
              term TEXT PRIMARY KEY,
              doc_freq INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )


def _init_graph_index() -> None:
    with _index_connect(config.GRAPH_INDEX) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
              entity TEXT NOT NULL,
              chunk_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              count INTEGER NOT NULL,
              indexed_at TEXT NOT NULL,
              PRIMARY KEY(entity, chunk_id)
            );
            CREATE INDEX IF NOT EXISTS idx_entities_entity ON entities(entity);
            """
        )


def _chunk_rows(source_id: str | None = None) -> list[sqlite3.Row]:
    where = ""
    args: list[Any] = []
    if source_id:
        where = "WHERE ch.source_id=?"
        args.append(source_id)
    with connect() as con:
        return con.execute(
            f"""
            SELECT ch.*, s.sensitivity, s.domain, s.topic
            FROM chunks ch
            JOIN sources s ON s.source_id = ch.source_id
            {where}
            ORDER BY ch.source_id, ch.chunk_index
            """,
            args,
        ).fetchall()


def hash_embedding(text: str, dimension: int = VECTOR_DIMENSION) -> list[float]:
    counts = [0.0] * dimension
    for token in tokenize(text):
        idx = int(sha256_text(token)[:8], 16) % dimension
        sign = 1.0 if int(sha256_text(token)[8:10], 16) % 2 == 0 else -1.0
        counts[idx] += sign
    norm = math.sqrt(sum(v * v for v in counts))
    if norm == 0:
        return counts
    return [v / norm for v in counts]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _openai_embeddings(texts: list[str]) -> list[list[float]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured in .env")
    payload = json.dumps({"model": config.API_VECTOR_MODEL, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:  # nosec - configured API endpoint
        body = json.loads(response.read().decode("utf-8"))
    return [item["embedding"] for item in body["data"]]


def build_local_vector_index(source_id: str | None = None) -> dict[str, Any]:
    init_db()
    _init_vector_index(config.LOCAL_VECTOR_INDEX)
    rows = _chunk_rows(source_id)
    indexed = 0
    with _index_connect(config.LOCAL_VECTOR_INDEX) as index_con, connect() as con:
        for row in rows:
            vector = hash_embedding(row["text"])
            index_con.execute(
                """
                INSERT INTO embeddings(chunk_id, source_id, text_hash, vector_json, dimension, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                  source_id=excluded.source_id,
                  text_hash=excluded.text_hash,
                  vector_json=excluded.vector_json,
                  dimension=excluded.dimension,
                  indexed_at=excluded.indexed_at
                """,
                (row["chunk_id"], row["source_id"], row["text_hash"], json.dumps(vector), len(vector), utc_now()),
            )
            upsert_coverage(
                con,
                row["chunk_id"],
                config.LOCAL_VECTOR_INDEX,
                is_indexed=True,
                vector_dimension=len(vector),
                content_hash=row["text_hash"],
            )
            indexed += 1
    return {"index": config.LOCAL_VECTOR_INDEX, "indexed": indexed, "path": str(_index_db_path(config.LOCAL_VECTOR_INDEX))}


def build_api_vector_index(source_id: str | None = None, *, include_private: bool = False) -> dict[str, Any]:
    init_db()
    _init_vector_index(config.API_VECTOR_INDEX)
    rows = [
        row
        for row in _chunk_rows(source_id)
        if include_private or row["sensitivity"] not in config.PRIVATE_SENSITIVITIES
    ]
    skipped_private = len(_chunk_rows(source_id)) - len(rows)
    indexed = 0
    errors = 0
    if not os.getenv("OPENAI_API_KEY"):
        with connect() as con:
            for row in rows:
                upsert_coverage(
                    con,
                    row["chunk_id"],
                    config.API_VECTOR_INDEX,
                    is_indexed=False,
                    index_error="OPENAI_API_KEY is not configured in .env",
                    content_hash=row["text_hash"],
                )
        return {
            "index": config.API_VECTOR_INDEX,
            "indexed": 0,
            "skipped_private": skipped_private,
            "errors": len(rows),
            "message": "API vector index not built because OPENAI_API_KEY is missing",
            "path": str(_index_db_path(config.API_VECTOR_INDEX)),
        }

    with _index_connect(config.API_VECTOR_INDEX) as index_con, connect() as con:
        for start in range(0, len(rows), 16):
            batch = rows[start : start + 16]
            try:
                vectors = _openai_embeddings([row["text"] for row in batch])
            except Exception as exc:
                errors += len(batch)
                for row in batch:
                    upsert_coverage(
                        con,
                        row["chunk_id"],
                        config.API_VECTOR_INDEX,
                        is_indexed=False,
                        index_error=str(exc),
                        content_hash=row["text_hash"],
                    )
                continue
            for row, vector in zip(batch, vectors):
                index_con.execute(
                    """
                    INSERT INTO embeddings(chunk_id, source_id, text_hash, vector_json, dimension, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                      source_id=excluded.source_id,
                      text_hash=excluded.text_hash,
                      vector_json=excluded.vector_json,
                      dimension=excluded.dimension,
                      indexed_at=excluded.indexed_at
                    """,
                    (
                        row["chunk_id"],
                        row["source_id"],
                        row["text_hash"],
                        json.dumps(vector),
                        len(vector),
                        utc_now(),
                    ),
                )
                upsert_coverage(
                    con,
                    row["chunk_id"],
                    config.API_VECTOR_INDEX,
                    is_indexed=True,
                    vector_dimension=len(vector),
                    content_hash=row["text_hash"],
                )
                indexed += 1
    return {
        "index": config.API_VECTOR_INDEX,
        "indexed": indexed,
        "skipped_private": skipped_private,
        "errors": errors,
        "path": str(_index_db_path(config.API_VECTOR_INDEX)),
    }


def build_bm25_index(source_id: str | None = None) -> dict[str, Any]:
    init_db()
    _init_bm25_index()
    rows = _chunk_rows(source_id)
    doc_freq: Counter[str] = Counter()
    docs = []
    for row in rows:
        tokens = tokenize(row["text"])
        docs.append((row, tokens))
        doc_freq.update(set(tokens))
    with _index_connect(config.BM25_INDEX) as index_con, connect() as con:
        if source_id is None:
            index_con.execute("DELETE FROM docs")
            index_con.execute("DELETE FROM stats")
            index_con.execute("DELETE FROM meta")
        else:
            for row, _tokens in docs:
                index_con.execute("DELETE FROM docs WHERE chunk_id=?", (row["chunk_id"],))
        for row, tokens in docs:
            index_con.execute(
                """
                INSERT INTO docs(chunk_id, source_id, text_hash, tokens_json, length, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                  source_id=excluded.source_id,
                  text_hash=excluded.text_hash,
                  tokens_json=excluded.tokens_json,
                  length=excluded.length,
                  indexed_at=excluded.indexed_at
                """,
                (row["chunk_id"], row["source_id"], row["text_hash"], json.dumps(tokens), len(tokens), utc_now()),
            )
            upsert_coverage(
                con,
                row["chunk_id"],
                config.BM25_INDEX,
                is_indexed=True,
                content_hash=row["text_hash"],
            )
        all_docs = index_con.execute("SELECT tokens_json FROM docs").fetchall()
        global_df: Counter[str] = Counter()
        total_len = 0
        for doc in all_docs:
            tokens = json.loads(doc["tokens_json"])
            global_df.update(set(tokens))
            total_len += len(tokens)
        index_con.execute("DELETE FROM stats")
        index_con.executemany(
            "INSERT INTO stats(term, doc_freq) VALUES (?, ?)",
            sorted(global_df.items()),
        )
        total_docs = len(all_docs)
        avgdl = total_len / total_docs if total_docs else 0
        index_con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('total_docs', ?), ('avgdl', ?)",
            (str(total_docs), str(avgdl)),
        )
    return {"index": config.BM25_INDEX, "indexed": len(rows), "path": str(_index_db_path(config.BM25_INDEX))}


def build_graph_index(source_id: str | None = None) -> dict[str, Any]:
    init_db()
    _init_graph_index()
    rows = _chunk_rows(source_id)
    indexed = 0
    with _index_connect(config.GRAPH_INDEX) as index_con, connect() as con:
        if source_id is None:
            index_con.execute("DELETE FROM entities")
        else:
            index_con.execute("DELETE FROM entities WHERE source_id=?", (source_id,))
        for row in rows:
            for entity, count in extract_entities(row["text"]):
                index_con.execute(
                    """
                    INSERT INTO entities(entity, chunk_id, source_id, count, indexed_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(entity, chunk_id) DO UPDATE SET
                      count=excluded.count,
                      indexed_at=excluded.indexed_at
                    """,
                    (entity.lower(), row["chunk_id"], row["source_id"], count, utc_now()),
                )
            upsert_coverage(
                con,
                row["chunk_id"],
                config.GRAPH_INDEX,
                is_indexed=True,
                content_hash=row["text_hash"],
            )
            indexed += 1
    return {"index": config.GRAPH_INDEX, "indexed": indexed, "path": str(_index_db_path(config.GRAPH_INDEX))}


def rebuild_indexes(
    *,
    index_names: list[str] | None = None,
    source_id: str | None = None,
    include_private_api: bool = False,
) -> dict[str, Any]:
    index_names = index_names or [config.LOCAL_VECTOR_INDEX, config.BM25_INDEX, config.GRAPH_INDEX]
    results = []
    for index_name in index_names:
        if index_name == config.LOCAL_VECTOR_INDEX:
            results.append(build_local_vector_index(source_id))
        elif index_name == config.API_VECTOR_INDEX:
            results.append(build_api_vector_index(source_id, include_private=include_private_api))
        elif index_name == config.BM25_INDEX:
            results.append(build_bm25_index(source_id))
        elif index_name == config.GRAPH_INDEX:
            results.append(build_graph_index(source_id))
        else:
            results.append({"index": index_name, "status": "unknown"})
    return {"status": "ready", "results": results}


def _filter_chunk_ids(filters: dict[str, Any] | None = None) -> set[str] | None:
    filters = filters or {}
    clauses = []
    args: list[Any] = []
    for key, column in (("domains", "s.domain"), ("sensitivities", "s.sensitivity"), ("source_ids", "s.source_id")):
        values = filters.get(key) or []
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            args.extend(values)
    topics = filters.get("topics") or []
    if topics:
        clauses.append("(" + " OR ".join("s.topic LIKE ?" for _ in topics) + ")")
        args.extend([f"%{topic}%" for topic in topics])
    if not clauses:
        return None
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT ch.chunk_id
            FROM chunks ch JOIN sources s ON s.source_id = ch.source_id
            WHERE {' AND '.join(clauses)}
            """,
            args,
        ).fetchall()
    return {row["chunk_id"] for row in rows}


def _metadata_for_chunks(chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT ch.chunk_id, ch.source_id, ch.text, ch.text_hash, ch.page_number,
                   ch.section_title, ch.timestamp_start, ch.timestamp_end,
                   s.original_filename, s.raw_path, s.domain, s.topic, s.sensitivity
            FROM chunks ch
            JOIN sources s ON s.source_id = ch.source_id
            WHERE ch.chunk_id IN ({placeholders})
            """,
            chunk_ids,
        ).fetchall()
    return {row["chunk_id"]: dict(row) for row in rows}


def search_local_vector(question: str, filters: dict[str, Any] | None = None, top_k: int = 12) -> list[dict[str, Any]]:
    path = _index_db_path(config.LOCAL_VECTOR_INDEX)
    if not path.exists():
        return []
    allowed = _filter_chunk_ids(filters)
    query_vector = hash_embedding(question)
    hits = []
    with _index_connect(config.LOCAL_VECTOR_INDEX) as con:
        rows = con.execute("SELECT * FROM embeddings").fetchall()
    for row in rows:
        if allowed is not None and row["chunk_id"] not in allowed:
            continue
        score = cosine(query_vector, json.loads(row["vector_json"]))
        if score > 0:
            hits.append({"chunk_id": row["chunk_id"], "source_id": row["source_id"], "score": score})
    hits.sort(key=lambda item: item["score"], reverse=True)
    return [{**hit, "found_by": "local_vector", "rank": idx + 1} for idx, hit in enumerate(hits[:top_k])]


def search_api_vector(
    question: str,
    filters: dict[str, Any] | None = None,
    top_k: int = 12,
    *,
    allow_private_api: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    filters = filters or {}
    if not allow_private_api and any(value in config.PRIVATE_SENSITIVITIES for value in filters.get("sensitivities", [])):
        return [], "API vector search skipped by privacy policy"
    path = _index_db_path(config.API_VECTOR_INDEX)
    if not path.exists():
        return [], "API vector index file does not exist"
    try:
        query_vector = _openai_embeddings([question])[0]
    except Exception as exc:
        return [], str(exc)
    allowed = _filter_chunk_ids(filters)
    hits = []
    with _index_connect(config.API_VECTOR_INDEX) as con:
        rows = con.execute("SELECT * FROM embeddings").fetchall()
    for row in rows:
        if allowed is not None and row["chunk_id"] not in allowed:
            continue
        score = cosine(query_vector, json.loads(row["vector_json"]))
        if score > 0:
            hits.append({"chunk_id": row["chunk_id"], "source_id": row["source_id"], "score": score})
    hits.sort(key=lambda item: item["score"], reverse=True)
    return [{**hit, "found_by": "api_vector", "rank": idx + 1} for idx, hit in enumerate(hits[:top_k])], None


def search_bm25(question: str, filters: dict[str, Any] | None = None, top_k: int = 12) -> list[dict[str, Any]]:
    path = _index_db_path(config.BM25_INDEX)
    if not path.exists():
        return []
    allowed = _filter_chunk_ids(filters)
    query_terms = tokenize(question)
    if not query_terms:
        return []
    with _index_connect(config.BM25_INDEX) as con:
        meta = {row["key"]: row["value"] for row in con.execute("SELECT key, value FROM meta").fetchall()}
        total_docs = int(float(meta.get("total_docs", "0") or 0))
        avgdl = float(meta.get("avgdl", "0") or 0)
        df = {row["term"]: row["doc_freq"] for row in con.execute("SELECT term, doc_freq FROM stats").fetchall()}
        rows = con.execute("SELECT * FROM docs").fetchall()
    if total_docs == 0 or avgdl == 0:
        return []
    k1 = 1.5
    b = 0.75
    hits = []
    for row in rows:
        if allowed is not None and row["chunk_id"] not in allowed:
            continue
        tokens = json.loads(row["tokens_json"])
        counts = Counter(tokens)
        score = 0.0
        for term in query_terms:
            if term not in counts:
                continue
            idf = math.log(1 + (total_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            tf = counts[term]
            denom = tf + k1 * (1 - b + b * row["length"] / avgdl)
            score += idf * (tf * (k1 + 1)) / denom
        if score > 0:
            hits.append({"chunk_id": row["chunk_id"], "source_id": row["source_id"], "score": score})
    hits.sort(key=lambda item: item["score"], reverse=True)
    return [{**hit, "found_by": "bm25", "rank": idx + 1} for idx, hit in enumerate(hits[:top_k])]


def search_graph(question: str, filters: dict[str, Any] | None = None, top_k: int = 12) -> list[dict[str, Any]]:
    path = _index_db_path(config.GRAPH_INDEX)
    if not path.exists():
        return []
    allowed = _filter_chunk_ids(filters)
    query_entities = {entity.lower() for entity, _count in extract_entities(question)}
    query_entities.update(token for token in tokenize(question) if len(token) > 3)
    if not query_entities:
        return []
    scores: defaultdict[str, float] = defaultdict(float)
    sources: dict[str, str] = {}
    with _index_connect(config.GRAPH_INDEX) as con:
        for entity in query_entities:
            rows = con.execute("SELECT * FROM entities WHERE entity=?", (entity,)).fetchall()
            for row in rows:
                if allowed is not None and row["chunk_id"] not in allowed:
                    continue
                scores[row["chunk_id"]] += row["count"]
                sources[row["chunk_id"]] = row["source_id"]
    hits = [
        {"chunk_id": chunk_id, "source_id": sources[chunk_id], "score": score}
        for chunk_id, score in scores.items()
    ]
    hits.sort(key=lambda item: item["score"], reverse=True)
    return [{**hit, "found_by": "graph", "rank": idx + 1} for idx, hit in enumerate(hits[:top_k])]


def search_full_scan(question: str, filters: dict[str, Any] | None = None, top_k: int = 20) -> list[dict[str, Any]]:
    allowed = _filter_chunk_ids(filters)
    terms = set(tokenize(question))
    if not terms:
        return []
    with connect() as con:
        rows = con.execute("SELECT chunk_id, source_id, text FROM chunks").fetchall()
    hits = []
    for row in rows:
        if allowed is not None and row["chunk_id"] not in allowed:
            continue
        tokens = tokenize(row["text"])
        score = sum(tokens.count(term) for term in terms)
        if score:
            hits.append({"chunk_id": row["chunk_id"], "source_id": row["source_id"], "score": float(score)})
    hits.sort(key=lambda item: item["score"], reverse=True)
    return [{**hit, "found_by": "full_scan", "rank": idx + 1} for idx, hit in enumerate(hits[:top_k])]


def fetch_metadata(chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    return _metadata_for_chunks(chunk_ids)
