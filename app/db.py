from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    config.ensure_runtime_dirs()
    con = sqlite3.connect(path or config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
              source_id TEXT PRIMARY KEY,
              original_filename TEXT NOT NULL,
              raw_path TEXT NOT NULL,
              raw_file_status TEXT NOT NULL DEFAULT 'retained',
              file_hash TEXT NOT NULL,
              file_size INTEGER NOT NULL,
              content_hash TEXT,
              normalized_text_hash TEXT,
              source_type TEXT NOT NULL,
              domain TEXT NOT NULL,
              topic TEXT,
              modality TEXT NOT NULL DEFAULT 'text',
              sensitivity TEXT NOT NULL DEFAULT 'public',
              language TEXT,
              created_at TEXT,
              ingested_at TEXT NOT NULL,
              parser_version TEXT,
              ingestion_status TEXT NOT NULL,
              duplicate_of TEXT,
              notes TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_file_hash
              ON sources(file_hash);
            CREATE INDEX IF NOT EXISTS idx_sources_domain
              ON sources(domain, sensitivity);

            CREATE TABLE IF NOT EXISTS chunks (
              chunk_id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              chunk_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              text_hash TEXT NOT NULL,
              page_number INTEGER,
              section_title TEXT,
              timestamp_start TEXT,
              timestamp_end TEXT,
              modality TEXT NOT NULL DEFAULT 'text',
              linked_figure_ids TEXT NOT NULL DEFAULT '[]',
              linked_table_ids TEXT NOT NULL DEFAULT '[]',
              linked_equation_ids TEXT NOT NULL DEFAULT '[]',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_source
              ON chunks(source_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_chunks_text_hash
              ON chunks(text_hash);

            CREATE TABLE IF NOT EXISTS multimodal_elements (
              element_id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              chunk_id TEXT,
              element_type TEXT NOT NULL,
              file_path TEXT,
              caption TEXT,
              page_number INTEGER,
              ocr_text TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE,
              FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS index_coverage (
              chunk_id TEXT NOT NULL,
              index_name TEXT NOT NULL,
              backend_type TEXT NOT NULL,
              model_name TEXT NOT NULL,
              index_version TEXT NOT NULL,
              is_indexed INTEGER NOT NULL DEFAULT 0,
              last_indexed_at TEXT,
              index_error TEXT,
              vector_dimension INTEGER,
              content_hash TEXT,
              PRIMARY KEY(chunk_id, index_name),
              FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_coverage_indexed
              ON index_coverage(index_name, is_indexed);

            CREATE TABLE IF NOT EXISTS parser_logs (
              log_id TEXT PRIMARY KEY,
              source_id TEXT,
              stage TEXT NOT NULL,
              status TEXT NOT NULL,
              message TEXT,
              details_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS query_logs (
              query_id TEXT PRIMARY KEY,
              question TEXT NOT NULL,
              retrieval_mode TEXT NOT NULL,
              analysis_model TEXT NOT NULL,
              filters_json TEXT NOT NULL DEFAULT '{}',
              retrieved_chunk_ids TEXT NOT NULL DEFAULT '[]',
              final_answer TEXT,
              created_at TEXT NOT NULL,
              latency_ms INTEGER,
              api_cost REAL
            );

            CREATE TABLE IF NOT EXISTS documents (
              document_id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              primary_source_id TEXT,
              document_hash TEXT NOT NULL,
              source_type TEXT NOT NULL,
              domain TEXT NOT NULL,
              topic TEXT,
              sensitivity TEXT NOT NULL,
              imported_at TEXT NOT NULL,
              processing_status TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(primary_source_id) REFERENCES sources(source_id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS source_files (
              file_id TEXT PRIMARY KEY,
              document_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              original_path TEXT NOT NULL,
              storage_path TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              imported_at TEXT NOT NULL,
              parser_version TEXT,
              extraction_status TEXT NOT NULL,
              indexing_status TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
              FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS parsed_artifacts (
              artifact_id TEXT PRIMARY KEY,
              document_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              artifact_type TEXT NOT NULL,
              artifact_path TEXT,
              status TEXT NOT NULL,
              parser_version TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
              FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS citations (
              citation_id TEXT PRIMARY KEY,
              chunk_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              document_id TEXT,
              source_file TEXT NOT NULL,
              page_number INTEGER,
              section_title TEXT,
              text_hash TEXT NOT NULL,
              quote TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE,
              FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS embedding_records (
              record_id TEXT PRIMARY KEY,
              chunk_id TEXT NOT NULL,
              index_name TEXT NOT NULL,
              backend_type TEXT NOT NULL,
              model_name TEXT NOT NULL,
              vector_dimension INTEGER,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS index_records (
              record_id TEXT PRIMARY KEY,
              chunk_id TEXT NOT NULL,
              index_name TEXT NOT NULL,
              backend_type TEXT NOT NULL,
              status TEXT NOT NULL,
              content_hash TEXT,
              last_indexed_at TEXT,
              error TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS retrieval_audits (
              audit_id TEXT PRIMARY KEY,
              query_text TEXT NOT NULL,
              retrieval_mode TEXT NOT NULL,
              created_at TEXT NOT NULL,
              selected_corpus_json TEXT NOT NULL DEFAULT '{}',
              backends_used_json TEXT NOT NULL DEFAULT '[]',
              backend_results_json TEXT NOT NULL DEFAULT '{}',
              merged_results_json TEXT NOT NULL DEFAULT '[]',
              dropped_duplicates_json TEXT NOT NULL DEFAULT '[]',
              answer_citations_json TEXT NOT NULL DEFAULT '[]',
              warning_flags_json TEXT NOT NULL DEFAULT '[]',
              skipped_json TEXT NOT NULL DEFAULT '[]',
              latency_ms INTEGER
            );

            CREATE TABLE IF NOT EXISTS retrieval_results (
              result_id TEXT PRIMARY KEY,
              audit_id TEXT NOT NULL,
              chunk_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              final_rank INTEGER NOT NULL,
              found_by_json TEXT NOT NULL DEFAULT '[]',
              ranks_json TEXT NOT NULL DEFAULT '{}',
              scores_json TEXT NOT NULL DEFAULT '{}',
              citation_id TEXT,
              duplicate_merged INTEGER NOT NULL DEFAULT 0,
              snippet TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(audit_id) REFERENCES retrieval_audits(audit_id) ON DELETE CASCADE,
              FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ingestion_jobs (
              job_id TEXT PRIMARY KEY,
              source_id TEXT,
              document_id TEXT,
              status TEXT NOT NULL,
              stage_status_json TEXT NOT NULL DEFAULT '{}',
              failure_reason TEXT,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              last_processed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS processing_errors (
              error_id TEXT PRIMARY KEY,
              source_id TEXT,
              document_id TEXT,
              stage TEXT NOT NULL,
              error_message TEXT NOT NULL,
              traceback TEXT,
              created_at TEXT NOT NULL,
              resolved INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS markdown_outputs (
              output_id TEXT PRIMARY KEY,
              output_type TEXT NOT NULL,
              title TEXT NOT NULL,
              question TEXT,
              selected_sources_json TEXT NOT NULL DEFAULT '[]',
              content TEXT NOT NULL,
              citations_json TEXT NOT NULL DEFAULT '[]',
              quality_checks_json TEXT NOT NULL DEFAULT '{}',
              llm_backend TEXT NOT NULL,
              created_at TEXT NOT NULL,
              file_path TEXT
            );

            CREATE TABLE IF NOT EXISTS output_templates (
              template_id TEXT PRIMARY KEY,
              output_type TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              template_markdown TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS local_llm_runs (
              run_id TEXT PRIMARY KEY,
              backend TEXT NOT NULL,
              model_name TEXT NOT NULL,
              prompt_hash TEXT NOT NULL,
              evidence_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
              status TEXT NOT NULL,
              error TEXT,
              created_at TEXT NOT NULL,
              latency_ms INTEGER
            );

            CREATE TABLE IF NOT EXISTS doi_download_jobs (
              job_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              input_count INTEGER NOT NULL DEFAULT 0,
              requested_count INTEGER NOT NULL DEFAULT 0,
              settings_json TEXT NOT NULL DEFAULT '{}',
              summary_json TEXT NOT NULL DEFAULT '{}',
              failure_reason TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS doi_download_items (
              id TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              doi TEXT NOT NULL,
              status TEXT NOT NULL,
              landing_url TEXT,
              publisher_domain TEXT,
              pdf_url TEXT,
              saved_path TEXT,
              metadata_path TEXT,
              file_hash TEXT,
              failure_reason TEXT,
              screenshot_path TEXT,
              html_snapshot_path TEXT,
              ingestion_source_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(job_id) REFERENCES doi_download_jobs(job_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_doi_download_items_job
              ON doi_download_items(job_id, status);
            CREATE INDEX IF NOT EXISTS idx_doi_download_items_doi
              ON doi_download_items(doi, status);

            CREATE TABLE IF NOT EXISTS doi_metadata (
              doi TEXT PRIMARY KEY,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              updated_at TEXT NOT NULL
            );
            """
        )
        _backfill_research_os_tables(con)


def _backfill_research_os_tables(con: sqlite3.Connection) -> None:
    rows = con.execute(
        """
        SELECT s.*
        FROM sources s
        WHERE NOT EXISTS (
          SELECT 1 FROM documents d WHERE d.primary_source_id = s.source_id
        )
        """
    ).fetchall()
    for row in rows:
        document_id = "doc_" + (row["file_hash"] or row["source_id"])[0:16]
        con.execute(
            """
            INSERT OR IGNORE INTO documents (
              document_id, title, primary_source_id, document_hash, source_type,
              domain, topic, sensitivity, imported_at, processing_status, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                Path(row["original_filename"]).stem,
                row["source_id"],
                row["file_hash"],
                row["source_type"],
                row["domain"],
                row["topic"],
                row["sensitivity"],
                row["ingested_at"],
                row["ingestion_status"],
                json_dumps({"backfilled_from": "sources", "content_hash": row["content_hash"]}),
            ),
        )
        con.execute(
            """
            INSERT OR IGNORE INTO source_files (
              file_id, document_id, source_id, original_path, storage_path,
              content_hash, imported_at, parser_version, extraction_status,
              indexing_status, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"file_{row['source_id']}",
                document_id,
                row["source_id"],
                row["original_filename"],
                row["raw_path"],
                row["file_hash"],
                row["ingested_at"],
                row["parser_version"],
                "parsed" if row["ingestion_status"] == "ready" else row["ingestion_status"],
                "unknown",
                json_dumps({"raw_file_status": row["raw_file_status"], "backfilled": True}),
            ),
        )
        con.execute(
            """
            INSERT OR IGNORE INTO ingestion_jobs (
              job_id, source_id, document_id, status, stage_status_json,
              failure_reason, started_at, finished_at, last_processed_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                f"job_backfill_{row['source_id']}",
                row["source_id"],
                document_id,
                row["ingestion_status"],
                json_dumps({"imported": "done", "parsed": row["ingestion_status"], "chunked": "unknown"}),
                row["ingested_at"],
                row["ingested_at"],
                row["ingested_at"],
            ),
        )


def dict_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def upsert_coverage(
    con: sqlite3.Connection,
    chunk_id: str,
    index_name: str,
    *,
    is_indexed: bool,
    index_error: str | None = None,
    vector_dimension: int | None = None,
    content_hash: str | None = None,
) -> None:
    definition = config.INDEX_DEFINITIONS[index_name]
    con.execute(
        """
        INSERT INTO index_coverage (
          chunk_id, index_name, backend_type, model_name, index_version,
          is_indexed, last_indexed_at, index_error, vector_dimension, content_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id, index_name) DO UPDATE SET
          backend_type=excluded.backend_type,
          model_name=excluded.model_name,
          index_version=excluded.index_version,
          is_indexed=excluded.is_indexed,
          last_indexed_at=excluded.last_indexed_at,
          index_error=excluded.index_error,
          vector_dimension=excluded.vector_dimension,
          content_hash=excluded.content_hash
        """,
        (
            chunk_id,
            index_name,
            definition["backend_type"],
            definition["model_name"],
            definition["index_version"],
            int(is_indexed),
            utc_now() if is_indexed else None,
            index_error,
            vector_dimension,
            content_hash,
        ),
    )
    record_id = f"idx_{chunk_id}_{index_name}"
    con.execute(
        """
        INSERT INTO index_records (
          record_id, chunk_id, index_name, backend_type, status, content_hash,
          last_indexed_at, error, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(record_id) DO UPDATE SET
          backend_type=excluded.backend_type,
          status=excluded.status,
          content_hash=excluded.content_hash,
          last_indexed_at=excluded.last_indexed_at,
          error=excluded.error,
          metadata_json=excluded.metadata_json
        """,
        (
            record_id,
            chunk_id,
            index_name,
            definition["backend_type"],
            "indexed" if is_indexed else "missing",
            content_hash,
            utc_now() if is_indexed else None,
            index_error,
            json_dumps({"index_version": definition["index_version"]}),
        ),
    )
    if definition["backend_type"] in {"local", "api"}:
        con.execute(
            """
            INSERT INTO embedding_records (
              record_id, chunk_id, index_name, backend_type, model_name,
              vector_dimension, status, created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
              vector_dimension=excluded.vector_dimension,
              status=excluded.status,
              created_at=excluded.created_at,
              metadata_json=excluded.metadata_json
            """,
            (
                f"emb_{chunk_id}_{index_name}",
                chunk_id,
                index_name,
                definition["backend_type"],
                definition["model_name"],
                vector_dimension,
                "indexed" if is_indexed else "missing",
                utc_now(),
                json_dumps({"error": index_error, "index_version": definition["index_version"]}),
            ),
        )


def ensure_chunk_coverage(con: sqlite3.Connection, chunk_ids: Iterable[str]) -> None:
    for chunk_id in chunk_ids:
        for index_name in config.INDEX_DEFINITIONS:
            definition = config.INDEX_DEFINITIONS[index_name]
            con.execute(
                """
                INSERT OR IGNORE INTO index_coverage (
                  chunk_id, index_name, backend_type, model_name, index_version, is_indexed
                )
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (
                    chunk_id,
                    index_name,
                    definition["backend_type"],
                    definition["model_name"],
                    definition["index_version"],
                ),
            )


def coverage_summary(con: sqlite3.Connection) -> dict[str, Any]:
    total_sources = con.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
    total_chunks = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    by_index = {}
    missing_by_index = {}
    stale = 0
    failed = 0
    for index_name in config.INDEX_DEFINITIONS:
        row = con.execute(
            """
            SELECT
              SUM(CASE WHEN is_indexed=1 THEN 1 ELSE 0 END) AS indexed,
              SUM(CASE WHEN is_indexed=0 THEN 1 ELSE 0 END) AS missing,
              SUM(CASE WHEN index_error IS NOT NULL AND index_error != '' THEN 1 ELSE 0 END) AS failed
            FROM index_coverage WHERE index_name=?
            """,
            (index_name,),
        ).fetchone()
        by_index[index_name] = int(row["indexed"] or 0)
        missing_by_index[index_name] = int(row["missing"] or 0)
        failed += int(row["failed"] or 0)
        stale_row = con.execute(
            """
            SELECT COUNT(*) AS n
            FROM index_coverage c
            JOIN chunks ch ON ch.chunk_id = c.chunk_id
            WHERE c.index_name=? AND c.is_indexed=1 AND c.content_hash IS NOT NULL
              AND c.content_hash != ch.text_hash
            """,
            (index_name,),
        ).fetchone()
        stale += int(stale_row["n"] or 0)
    missing_all = con.execute(
        """
        SELECT COUNT(*) AS n FROM chunks ch
        WHERE NOT EXISTS (
          SELECT 1 FROM index_coverage c
          WHERE c.chunk_id = ch.chunk_id AND c.is_indexed = 1
        )
        """
    ).fetchone()["n"]
    return {
        "total_sources": total_sources,
        "total_chunks": total_chunks,
        "indexed_by": by_index,
        "missing_by": missing_by_index,
        "missing_all_indexes": int(missing_all or 0),
        "stale_indexes": stale,
        "failed_chunks": failed,
    }
