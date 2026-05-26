from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from . import config
from .db import connect, coverage_summary, init_db, json_dumps, json_loads, utc_now


def dashboard() -> dict[str, Any]:
    init_db()
    with connect() as con:
        counts = {
            "sources": con.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"],
            "chunks": con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"],
            "papers": con.execute("SELECT COUNT(*) AS n FROM sources WHERE domain='paper'").fetchone()["n"],
            "multimodal_elements": con.execute("SELECT COUNT(*) AS n FROM multimodal_elements").fetchone()["n"],
            "failed_sources": con.execute("SELECT COUNT(*) AS n FROM sources WHERE ingestion_status='failed'").fetchone()["n"],
        }
        recent = [
            dict(row)
            for row in con.execute(
                """
                SELECT source_id, original_filename, domain, topic, sensitivity, ingestion_status, ingested_at
                FROM sources ORDER BY ingested_at DESC LIMIT 8
                """
            ).fetchall()
        ]
        failed = [
            dict(row)
            for row in con.execute(
                """
                SELECT log_id, source_id, stage, status, message, created_at
                FROM parser_logs WHERE status='failed'
                ORDER BY created_at DESC LIMIT 20
                """
            ).fetchall()
        ]
    with connect() as con:
        summary = coverage_summary(con)
    return {
        "counts": counts,
        "coverage": summary,
        "recent_ingestions": recent,
        "failed_ingestions": failed,
        "backend_status": {
            "database": str(config.DB_PATH),
            "raw_data": str(config.RAW_DIR),
            "indexes": str(config.INDEX_DIR),
            "local_models": str(config.LOCAL_MODELS_DIR),
            "api_key_configured": bool(__import__("os").getenv("OPENAI_API_KEY")),
        },
    }


def list_sources() -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT s.*,
              (SELECT COUNT(*) FROM chunks ch WHERE ch.source_id=s.source_id) AS chunk_count,
              (SELECT COUNT(*) FROM multimodal_elements e WHERE e.source_id=s.source_id) AS element_count
            FROM sources s
            ORDER BY ingested_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_documents() -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT d.*,
              s.original_filename,
              s.raw_path,
              s.raw_file_status,
              (SELECT COUNT(*) FROM chunks ch WHERE ch.source_id=d.primary_source_id) AS chunk_count
            FROM documents d
            LEFT JOIN sources s ON s.source_id=d.primary_source_id
            ORDER BY imported_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def processing_status() -> dict[str, Any]:
    init_db()
    with connect() as con:
        rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT d.document_id, d.title, d.primary_source_id AS source_id,
                       d.domain, d.topic, d.sensitivity, d.processing_status,
                       s.original_filename, s.raw_path, s.file_hash, s.content_hash,
                       s.parser_version, s.ingestion_status,
                       sf.extraction_status, sf.indexing_status,
                       ij.status AS job_status, ij.stage_status_json,
                       ij.failure_reason, ij.last_processed_at,
                       (SELECT COUNT(*) FROM chunks ch WHERE ch.source_id=s.source_id) AS chunk_count,
                       (SELECT COUNT(*) FROM index_coverage ic
                        JOIN chunks ch ON ch.chunk_id=ic.chunk_id
                        WHERE ch.source_id=s.source_id AND ic.is_indexed=0) AS missing_index_count
                FROM documents d
                LEFT JOIN sources s ON s.source_id=d.primary_source_id
                LEFT JOIN source_files sf ON sf.source_id=s.source_id
                LEFT JOIN ingestion_jobs ij ON ij.source_id=s.source_id
                ORDER BY d.imported_at DESC
                """
            ).fetchall()
        ]
        errors = [
            dict(row)
            for row in con.execute(
                """
                SELECT error_id, source_id, document_id, stage, error_message, created_at, resolved
                FROM processing_errors
                ORDER BY created_at DESC LIMIT 50
                """
            ).fetchall()
        ]
    return {"documents": rows, "errors": errors}


def retrieval_audits(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT audit_id, query_text, retrieval_mode, created_at, selected_corpus_json,
                   backends_used_json, dropped_duplicates_json, answer_citations_json,
                   warning_flags_json, latency_ms
            FROM retrieval_audits
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def retrieval_audit_detail(audit_id: str) -> dict[str, Any]:
    init_db()
    with connect() as con:
        audit = con.execute(
            """
            SELECT audit_id, query_text, retrieval_mode, created_at, selected_corpus_json,
                   backends_used_json, backend_results_json, merged_results_json,
                   dropped_duplicates_json, answer_citations_json, warning_flags_json,
                   skipped_json, latency_ms
            FROM retrieval_audits
            WHERE audit_id=?
            """,
            (audit_id,),
        ).fetchone()
        if not audit:
            return {"status": "failed", "message": "audit not found", "audit_id": audit_id}
        results = [
            dict(row)
            for row in con.execute(
                """
                SELECT rr.result_id, rr.audit_id, rr.chunk_id, rr.source_id, rr.final_rank,
                       rr.found_by_json, rr.ranks_json, rr.scores_json, rr.citation_id,
                       rr.duplicate_merged, rr.snippet, rr.created_at,
                       ch.page_number, ch.section_title,
                       s.original_filename, s.raw_path, s.domain, s.topic, s.sensitivity
                FROM retrieval_results rr
                LEFT JOIN chunks ch ON ch.chunk_id=rr.chunk_id
                LEFT JOIN sources s ON s.source_id=rr.source_id
                WHERE rr.audit_id=?
                ORDER BY rr.final_rank
                """,
                (audit_id,),
            ).fetchall()
        ]
    audit_dict = dict(audit)
    for key, default in {
        "selected_corpus_json": {},
        "backends_used_json": [],
        "backend_results_json": {},
        "merged_results_json": [],
        "dropped_duplicates_json": [],
        "answer_citations_json": [],
        "warning_flags_json": [],
        "skipped_json": [],
    }.items():
        audit_dict[key.removesuffix("_json")] = json_loads(audit_dict.pop(key), default)
    for row in results:
        row["found_by"] = json_loads(row.pop("found_by_json"), [])
        row["ranks"] = json_loads(row.pop("ranks_json"), {})
        row["scores"] = json_loads(row.pop("scores_json"), {})
    return {"status": "ready", "audit": audit_dict, "results": results}


def source_chunks(source_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT chunk_id, source_id, chunk_index, page_number, section_title, text_hash,
                   substr(text, 1, 900) AS text_preview, metadata_json
            FROM chunks WHERE source_id=?
            ORDER BY chunk_index
            """,
            (source_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_source_metadata(source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"domain", "topic", "sensitivity", "notes"}
    updates = {key: payload[key] for key in allowed if key in payload}
    if not updates:
        return {"status": "noop", "message": "No editable fields supplied"}
    assignments = ", ".join(f"{key}=?" for key in updates)
    with connect() as con:
        con.execute(
            f"UPDATE sources SET {assignments} WHERE source_id=?",
            [*updates.values(), source_id],
        )
        row = con.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
    return {"status": "ready", "source": dict(row) if row else None}


def coverage_detail(limit: int = 200) -> dict[str, Any]:
    init_db()
    with connect() as con:
        missing = [
            dict(row)
            for row in con.execute(
                """
                SELECT c.chunk_id, c.index_name, c.backend_type, c.model_name, c.index_error,
                       ch.source_id, ch.page_number, s.original_filename, s.sensitivity
                FROM index_coverage c
                JOIN chunks ch ON ch.chunk_id=c.chunk_id
                JOIN sources s ON s.source_id=ch.source_id
                WHERE c.is_indexed=0
                ORDER BY c.index_name, s.original_filename, ch.chunk_index
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        stale = [
            dict(row)
            for row in con.execute(
                """
                SELECT c.chunk_id, c.index_name, c.backend_type, ch.source_id, ch.page_number,
                       s.original_filename
                FROM index_coverage c
                JOIN chunks ch ON ch.chunk_id=c.chunk_id
                JOIN sources s ON s.source_id=ch.source_id
                WHERE c.is_indexed=1 AND c.content_hash IS NOT NULL AND c.content_hash != ch.text_hash
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        failed = [
            dict(row)
            for row in con.execute(
                """
                SELECT c.chunk_id, c.index_name, c.index_error, ch.source_id, ch.page_number,
                       s.original_filename
                FROM index_coverage c
                JOIN chunks ch ON ch.chunk_id=c.chunk_id
                JOIN sources s ON s.source_id=ch.source_id
                WHERE c.index_error IS NOT NULL AND c.index_error != ''
                ORDER BY c.index_name
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
    with connect() as con:
        summary = coverage_summary(con)
    return {"summary": summary, "missing": missing, "stale": stale, "failed": failed}


def detect_duplicates() -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT file_hash, COUNT(*) AS n, GROUP_CONCAT(source_id) AS source_ids,
                   GROUP_CONCAT(original_filename) AS filenames
            FROM sources
            GROUP BY file_hash HAVING COUNT(*) > 1
            """
        ).fetchall()
    return [dict(row) for row in rows]


def backup_database() -> dict[str, Any]:
    init_db()
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "").replace("+", "_")
    backup_path = config.BACKUP_DIR / f"knowledge_{stamp}.sqlite"
    shutil.copy2(config.DB_PATH, backup_path)
    export_path = config.BACKUP_DIR / f"metadata_{stamp}.json"
    with connect() as con:
        payload = {
            "sources": [dict(row) for row in con.execute("SELECT * FROM sources").fetchall()],
            "chunks": [
                {
                    **dict(row),
                    "text": row["text"][:300],
                }
                for row in con.execute("SELECT * FROM chunks").fetchall()
            ],
            "coverage": [dict(row) for row in con.execute("SELECT * FROM index_coverage").fetchall()],
        }
    export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ready", "backup_path": str(backup_path), "metadata_export_path": str(export_path)}


def maintenance_report() -> dict[str, Any]:
    init_db()
    with connect() as con:
        coverage = coverage_summary(con)
        source_count = con.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
        document_count = con.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        failed_jobs = con.execute("SELECT COUNT(*) AS n FROM ingestion_jobs WHERE status='failed'").fetchone()["n"]
        missing_originals = []
        for row in con.execute("SELECT source_id, original_filename, raw_path FROM sources").fetchall():
            if row["raw_path"] and not Path(row["raw_path"]).exists():
                missing_originals.append(dict(row))
        recent_outputs = [
            dict(row)
            for row in con.execute(
                """
                SELECT output_id, output_type, title, file_path, created_at
                FROM markdown_outputs ORDER BY created_at DESC LIMIT 10
                """
            ).fetchall()
        ]
        audit_count = con.execute("SELECT COUNT(*) AS n FROM retrieval_audits").fetchone()["n"]
    storage = {}
    for label, path in {
        "raw": config.RAW_DIR,
        "db": config.DB_DIR,
        "indexes": config.INDEX_DIR,
        "outputs": config.OUTPUT_DIR,
    }.items():
        total = 0
        if path.exists():
            for file in path.rglob("*"):
                if file.is_file():
                    total += file.stat().st_size
        storage[label] = {"path": str(path), "bytes": total}
    return {
        "created_at": utc_now(),
        "system_health": "ok" if not failed_jobs and not missing_originals else "needs_review",
        "database": {"path": str(config.DB_PATH), "sources": source_count, "documents": document_count},
        "coverage": coverage,
        "storage": storage,
        "api_usage": {"configured": bool(__import__("os").getenv("OPENAI_API_KEY")), "estimated_cost": None},
        "failed_jobs": failed_jobs,
        "duplicates": detect_duplicates(),
        "missing_original_files": missing_originals,
        "retrieval_audit_count": audit_count,
        "recent_outputs": recent_outputs,
        "rule_files": {"project_rule_files_found": []},
    }


def generate_codex_repair_guidance(reason: str = "") -> dict[str, Any]:
    report = maintenance_report()
    output_dir = config.OUTPUT_DIR / "maintenance"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"codex_repair_guidance_{uuid.uuid4().hex[:8]}.md"
    content = f"""# Codex Repair Guidance

## 1. Reason
{reason or "Maintenance center generated this task from current health state."}

## 2. Current Health
```json
{json.dumps(report, ensure_ascii=False, indent=2)}
```

## 3. Suggested Codex Task
- Inspect failed ingestion jobs and processing errors.
- Rebuild missing indexes from canonical chunks.
- Verify missing original files before deleting or re-importing anything.
- Run unit tests after repair.
- Produce a Chinese final report with changed files and residual risk.

## 4. Safety Rules
- Do not delete raw files automatically.
- Do not send private documents to API.
- Do not overwrite rule files blindly.
"""
    path.write_text(content, encoding="utf-8")
    return {"status": "ready", "file_path": str(path), "content": content}


def generate_codex_repair_from_audit(audit_id: str, expected_behavior: str = "") -> dict[str, Any]:
    detail = retrieval_audit_detail(audit_id)
    if detail.get("status") != "ready":
        return detail
    audit = detail["audit"]
    results = detail["results"]
    output_dir = config.OUTPUT_DIR / "maintenance"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"audit_repair_{audit_id}.md"
    weak_signals = []
    if audit.get("warning_flags"):
        weak_signals.extend(audit["warning_flags"])
    if not results:
        weak_signals.append("no retrieval_results rows for this audit")
    if results and not any(len(row.get("found_by", [])) > 1 for row in results):
        weak_signals.append("no duplicate backend convergence; inspect index coverage and ranking")
    result_lines = "\n".join(
        f"- rank {row['final_rank']}: `{row['chunk_id']}` / `{row['source_id']}` / "
        f"{row.get('original_filename') or 'unknown'} / page {row.get('page_number') or 'unknown'} / "
        f"found_by={row.get('found_by', [])} / citation={row.get('citation_id')}"
        for row in results[:20]
    )
    content = f"""# Retrieval Audit Repair Guidance

## 1. Audit
- audit_id: `{audit_id}`
- created_at: {audit.get("created_at")}
- retrieval_mode: `{audit.get("retrieval_mode")}`
- query: {audit.get("query_text")}
- expected_behavior: {expected_behavior or "未提供。请根据用户认为缺失/错误的证据补充。"}

## 2. Observed Evidence
{result_lines or "- No retrieval results were recorded."}

## 3. Warnings / Weak Signals
{chr(10).join(f"- {item}" for item in weak_signals) if weak_signals else "- none"}

## 4. Backend Results
```json
{json.dumps(audit.get("backend_results", {}), ensure_ascii=False, indent=2)}
```

## 5. Duplicates And Citations
```json
{json.dumps({"dropped_duplicates": audit.get("dropped_duplicates", []), "answer_citations": audit.get("answer_citations", [])}, ensure_ascii=False, indent=2)}
```

## 6. Suggested Codex Repair Task
1. Reproduce the query with `/api/retrieval-audit?audit_id={audit_id}` and inspect missing or weak evidence.
2. Check `app/retrieval.py` merge/rerank behavior and `app/indexes.py` backend search behavior.
3. Check Maintenance index coverage for chunks that should have been returned.
4. If the expected source is missing, inspect ingestion/chunking before changing ranking.
5. Add or update tests that assert the missing source/chunk is retrievable and deduplicated by `chunk_id`.

## 7. Safety Rules
- Do not delete raw files.
- Do not send private/confidential sources to API during reproduction.
- Keep local vector and API vector indexes separate.
"""
    path.write_text(content, encoding="utf-8")
    return {"status": "ready", "file_path": str(path), "content": content, "audit_id": audit_id}
