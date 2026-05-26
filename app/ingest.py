from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import config
from .db import connect, ensure_chunk_coverage, init_db, json_dumps, utc_now
from .parsing import chunk_page_text, parse_pdf
from .text_utils import detect_language, file_hash, normalize_text, safe_filename, sha256_text


@dataclass
class IngestResult:
    source_id: str | None
    status: str
    duplicate: bool
    duplicate_of: str | None
    chunks_created: int
    raw_path: str | None
    message: str
    source: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _raw_folder(domain: str) -> Path:
    return config.RAW_DIR / config.DOMAIN_RAW_FOLDERS.get(domain, "misc")


def _source_type(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "pdf":
        return "pdf"
    if ext in {"png", "jpg", "jpeg", "webp", "tif", "tiff"}:
        return "image"
    if ext in {"md", "txt"}:
        return "text"
    return ext or "unknown"


def _log(source_id: str | None, stage: str, status: str, message: str, details: dict[str, Any] | None = None) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO parser_logs(log_id, source_id, stage, status, message, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (f"log_{uuid.uuid4().hex}", source_id, stage, status, message, json_dumps(details or {}), utc_now()),
        )


def ingest_file(
    input_path: Path,
    *,
    domain: str = "paper",
    topic: str = "",
    sensitivity: str = "public",
    auto_index: bool = True,
) -> IngestResult:
    init_db()
    input_path = Path(input_path)
    if not input_path.exists() or not input_path.is_file():
        return IngestResult(None, "failed", False, None, 0, None, f"File not found: {input_path}")

    digest = file_hash(input_path)
    size = input_path.stat().st_size
    with connect() as con:
        existing = con.execute(
            "SELECT source_id, original_filename, raw_path FROM sources WHERE file_hash=?",
            (digest,),
        ).fetchone()
        if existing:
            return IngestResult(
                existing["source_id"],
                "duplicate",
                True,
                existing["source_id"],
                0,
                existing["raw_path"],
                f"Duplicate file_hash; existing source is {existing['source_id']}",
                dict(existing),
            )

    source_id = "src_" + digest[:16]
    document_id = "doc_" + digest[:16]
    job_id = f"job_{uuid.uuid4().hex}"
    raw_folder = _raw_folder(domain)
    raw_folder.mkdir(parents=True, exist_ok=True)
    raw_path = raw_folder / f"{source_id}_{safe_filename(input_path.name)}"
    if raw_path.resolve() != input_path.resolve():
        shutil.copy2(input_path, raw_path)

    source_type = _source_type(input_path)
    parser_version = config.PARSER_VERSION if source_type == "pdf" else "reserved_future_parser"
    now = utc_now()

    try:
        if source_type != "pdf":
            raise RuntimeError(
                f"{source_type} ingestion is reserved in schema, but only PDF papers are implemented in this MVP"
            )
        parsed = parse_pdf(raw_path)
        language = detect_language(parsed.language_sample)
        chunks: list[dict[str, Any]] = []
        chunk_index = 0
        for page in parsed.pages:
            for local_idx, text in enumerate(chunk_page_text(page.text), start=1):
                text = normalize_text(text)
                chunk_id = f"{source_id}_p{page.page_number:04d}_c{local_idx:03d}"
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "source_id": source_id,
                        "chunk_index": chunk_index,
                        "text": text,
                        "text_hash": sha256_text(text),
                        "page_number": page.page_number,
                        "section_title": page.section_title,
                        "metadata_json": json_dumps(
                            {
                                "parser": parsed.parser_name,
                                "chunking_version": config.CHUNKING_VERSION,
                                "page_image_count": page.image_count,
                            }
                        ),
                    }
                )
                chunk_index += 1

        with connect() as con:
            con.execute(
                """
                INSERT INTO ingestion_jobs (
                  job_id, source_id, document_id, status, stage_status_json,
                  failure_reason, started_at, finished_at, last_processed_at
                )
                VALUES (?, ?, ?, 'running', ?, NULL, ?, NULL, ?)
                """,
                (
                    job_id,
                    source_id,
                    document_id,
                    json_dumps(
                        {
                            "imported": "done",
                            "parsed": "running",
                            "ocr": "not_applicable",
                            "chunked": "pending",
                            "local_embedded": "pending",
                            "api_embedded": "pending",
                            "bm25_indexed": "pending",
                            "graph_extracted": "pending",
                        }
                    ),
                    now,
                    now,
                ),
            )
            con.execute(
                """
                INSERT INTO sources (
                  source_id, original_filename, raw_path, raw_file_status, file_hash, file_size,
                  content_hash, normalized_text_hash, source_type, domain, topic, modality,
                  sensitivity, language, created_at, ingested_at, parser_version,
                  ingestion_status, duplicate_of, notes
                )
                VALUES (?, ?, ?, 'retained', ?, ?, ?, ?, ?, ?, ?, 'text', ?, ?, ?, ?, ?, 'ready', NULL, ?)
                """,
                (
                    source_id,
                    input_path.name,
                    str(raw_path),
                    digest,
                    size,
                    parsed.content_hash,
                    parsed.content_hash,
                    source_type,
                    domain,
                    topic,
                    sensitivity,
                    language,
                    parsed.metadata.get("creationDate"),
                    now,
                    parser_version,
                    json_dumps({"pdf_metadata": parsed.metadata}),
                ),
            )
            con.execute(
                """
                INSERT INTO documents (
                  document_id, title, primary_source_id, document_hash, source_type,
                  domain, topic, sensitivity, imported_at, processing_status, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'chunked', ?)
                ON CONFLICT(document_id) DO UPDATE SET
                  title=excluded.title,
                  primary_source_id=excluded.primary_source_id,
                  processing_status=excluded.processing_status,
                  metadata_json=excluded.metadata_json
                """,
                (
                    document_id,
                    input_path.stem,
                    source_id,
                    digest,
                    source_type,
                    domain,
                    topic,
                    sensitivity,
                    now,
                    json_dumps({"language": language, "content_hash": parsed.content_hash}),
                ),
            )
            con.execute(
                """
                INSERT INTO source_files (
                  file_id, document_id, source_id, original_path, storage_path,
                  content_hash, imported_at, parser_version, extraction_status,
                  indexing_status, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'parsed', 'pending', ?)
                ON CONFLICT(file_id) DO UPDATE SET
                  extraction_status=excluded.extraction_status,
                  indexing_status=excluded.indexing_status,
                  metadata_json=excluded.metadata_json
                """,
                (
                    f"file_{source_id}",
                    document_id,
                    source_id,
                    str(input_path),
                    str(raw_path),
                    digest,
                    now,
                    parser_version,
                    json_dumps({"raw_file_status": "retained"}),
                ),
            )
            con.execute(
                """
                INSERT INTO parsed_artifacts (
                  artifact_id, document_id, source_id, artifact_type, artifact_path,
                  status, parser_version, metadata_json, created_at
                )
                VALUES (?, ?, ?, 'canonical_chunks', NULL, 'ready', ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                  status=excluded.status,
                  metadata_json=excluded.metadata_json
                """,
                (
                    f"artifact_{source_id}_chunks",
                    document_id,
                    source_id,
                    parser_version,
                    json_dumps({"chunks_created": len(chunks), "content_hash": parsed.content_hash}),
                    now,
                ),
            )
            con.executemany(
                """
                INSERT INTO chunks (
                  chunk_id, source_id, chunk_index, text, text_hash, page_number, section_title,
                  timestamp_start, timestamp_end, modality, linked_figure_ids, linked_table_ids,
                  linked_equation_ids, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'text', '[]', '[]', '[]', ?, ?)
                """,
                [
                    (
                        chunk["chunk_id"],
                        chunk["source_id"],
                        chunk["chunk_index"],
                        chunk["text"],
                        chunk["text_hash"],
                        chunk["page_number"],
                        chunk["section_title"],
                        chunk["metadata_json"],
                        now,
                    )
                    for chunk in chunks
                ],
            )
            for page in parsed.pages:
                for image_idx in range(page.image_count):
                    con.execute(
                        """
                        INSERT INTO multimodal_elements (
                          element_id, source_id, chunk_id, element_type, file_path,
                          caption, page_number, ocr_text, metadata_json
                        )
                        VALUES (?, ?, NULL, 'image', NULL, NULL, ?, NULL, ?)
                        """,
                        (
                            f"{source_id}_p{page.page_number:04d}_image_{image_idx + 1:03d}",
                            source_id,
                            page.page_number,
                            json_dumps({"status": "placeholder", "parser": parsed.parser_name}),
                        ),
                    )
                for caption_idx, caption in enumerate(page.captions, start=1):
                    con.execute(
                        """
                        INSERT INTO multimodal_elements (
                          element_id, source_id, chunk_id, element_type, file_path,
                          caption, page_number, ocr_text, metadata_json
                        )
                        VALUES (?, ?, NULL, 'caption', NULL, ?, ?, NULL, ?)
                        """,
                        (
                            f"{source_id}_p{page.page_number:04d}_caption_{caption_idx:03d}",
                            source_id,
                            caption,
                            page.page_number,
                            json_dumps({"status": "parsed_caption"}),
                        ),
                    )
            ensure_chunk_coverage(con, [chunk["chunk_id"] for chunk in chunks])
            con.execute(
                """
                UPDATE ingestion_jobs
                SET status='ready',
                    stage_status_json=?,
                    finished_at=?,
                    last_processed_at=?
                WHERE job_id=?
                """,
                (
                    json_dumps(
                        {
                            "imported": "done",
                            "parsed": "done",
                            "ocr": "not_applicable",
                            "chunked": "done",
                            "local_embedded": "pending",
                            "api_embedded": "pending",
                            "bm25_indexed": "pending",
                            "graph_extracted": "pending",
                        }
                    ),
                    utc_now(),
                    utc_now(),
                    job_id,
                ),
            )

        _log(source_id, "ingest", "ready", f"Created {len(chunks)} canonical chunks")
        if auto_index and chunks:
            from .indexes import rebuild_indexes

            rebuild_indexes(index_names=[config.LOCAL_VECTOR_INDEX, config.BM25_INDEX, config.GRAPH_INDEX], source_id=source_id)

        with connect() as con:
            source = con.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return IngestResult(
            source_id,
            "ready",
            False,
            None,
            len(chunks),
            str(raw_path),
            "Ingested PDF into canonical structured data layer",
            dict(source) if source else None,
        )
    except Exception as exc:
        with connect() as con:
            con.execute(
                """
                INSERT INTO sources (
                  source_id, original_filename, raw_path, raw_file_status, file_hash, file_size,
                  source_type, domain, topic, modality, sensitivity, ingested_at, parser_version,
                  ingestion_status, notes
                )
                VALUES (?, ?, ?, 'retained', ?, ?, ?, ?, ?, 'text', ?, ?, ?, 'failed', ?)
                """,
                (
                    source_id,
                    input_path.name,
                    str(raw_path),
                    digest,
                    size,
                    source_type,
                    domain,
                    topic,
                    sensitivity,
                    now,
                    parser_version,
                    json_dumps({"error": str(exc)}),
                ),
            )
            con.execute(
                """
                INSERT OR REPLACE INTO ingestion_jobs (
                  job_id, source_id, document_id, status, stage_status_json,
                  failure_reason, started_at, finished_at, last_processed_at
                )
                VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    source_id,
                    document_id,
                    json_dumps({"imported": "done", "parsed": "failed"}),
                    str(exc),
                    now,
                    utc_now(),
                    utc_now(),
                ),
            )
            con.execute(
                """
                INSERT INTO processing_errors (
                  error_id, source_id, document_id, stage, error_message, traceback, created_at, resolved
                )
                VALUES (?, ?, ?, 'ingest', ?, NULL, ?, 0)
                """,
                (f"err_{uuid.uuid4().hex}", source_id, document_id, str(exc), utc_now()),
            )
        _log(source_id, "ingest", "failed", str(exc))
        return IngestResult(source_id, "failed", False, None, 0, str(raw_path), str(exc))


def ingest_folder(
    folder: Path,
    *,
    domain: str = "paper",
    topic: str = "",
    sensitivity: str = "public",
) -> dict[str, Any]:
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return {"status": "failed", "message": f"Folder not found: {folder}", "results": []}
    results = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() == ".pdf":
            results.append(
                ingest_file(path, domain=domain, topic=topic, sensitivity=sensitivity).to_dict()
            )
    return {"status": "ready", "message": f"Processed {len(results)} PDFs", "results": results}
