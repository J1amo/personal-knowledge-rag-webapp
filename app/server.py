from __future__ import annotations

import cgi
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from .db import init_db
from .doi_downloader import (
    clear_browser_profile,
    doi_downloader_status,
    list_doi_download_items,
    list_doi_download_jobs,
    run_doi_download_job,
)
from .indexes import rebuild_indexes
from .ingest import ingest_file, ingest_folder
from .literature_discovery import discover_literature
from .maintenance import (
    backup_database,
    coverage_detail,
    dashboard,
    detect_duplicates,
    generate_codex_repair_from_audit,
    generate_codex_repair_guidance,
    list_documents,
    list_sources,
    maintenance_report,
    processing_status,
    retrieval_audit_detail,
    retrieval_audits,
    source_chunks,
    update_source_metadata,
)
from .output_studio import (
    check_local_llm,
    generate_markdown_output,
    generate_project_markdown_output,
    list_available_output_types,
    list_markdown_outputs,
)
from .research_packs import list_packs, load_pack
from .research_projects import (
    add_source_to_project,
    answer_query_for_project,
    create_project,
    list_project_sources,
    list_projects,
    remove_source_from_project,
)
from .retrieval import answer_query, compare_modes

STATIC_DIR = config.PROJECT_ROOT / "static"


class JsonError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "PersonalKnowledgeBase/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        suffix = path.suffix.lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".mjs": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".map": "application/json; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".wasm": "application/wasm",
            ".bcmap": "application/octet-stream",
            ".pfb": "application/octet-stream",
            ".ttf": "font/ttf",
            ".otf": "font/otf",
        }.get(suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw_path(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        content_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise JsonError(400, f"Invalid JSON: {exc}") from exc

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/health":
                init_db()
                self._send_json({"status": "ok", "project_root": str(config.PROJECT_ROOT)})
            elif path == "/api/dashboard":
                self._send_json(dashboard())
            elif path == "/api/sources":
                self._send_json({"sources": list_sources()})
            elif path == "/api/documents":
                self._send_json({"documents": list_documents()})
            elif path == "/api/processing-status":
                self._send_json(processing_status())
            elif path == "/api/retrieval-audits":
                self._send_json({"audits": retrieval_audits()})
            elif path == "/api/retrieval-audit":
                qs = parse_qs(parsed.query)
                audit_id = (qs.get("audit_id") or [""])[0]
                if not audit_id:
                    raise JsonError(400, "audit_id is required")
                self._send_json(retrieval_audit_detail(audit_id))
            elif path == "/api/outputs":
                self._send_json({"outputs": list_markdown_outputs()})
            elif path == "/api/local-llm/status":
                self._send_json(check_local_llm())
            elif path == "/api/research/projects":
                self._send_json({"projects": list_projects()})
            elif path == "/api/research/project/sources":
                qs = parse_qs(parsed.query)
                project_id = (qs.get("project_id") or [""])[0]
                if not project_id:
                    raise JsonError(400, "project_id is required")
                self._send_json({"sources": list_project_sources(project_id)})
            elif path == "/api/research/packs":
                self._send_json({"packs": list_packs()})
            elif path == "/api/research/pack":
                qs = parse_qs(parsed.query)
                pack_id = (qs.get("pack_id") or [""])[0]
                if not pack_id:
                    raise JsonError(400, "pack_id is required")
                try:
                    self._send_json(load_pack(pack_id))
                except FileNotFoundError as exc:
                    raise JsonError(404, str(exc)) from exc
                except (KeyError, ValueError) as exc:
                    raise JsonError(400, str(exc)) from exc
            elif path == "/api/research/output-types":
                qs = parse_qs(parsed.query)
                project_id = (qs.get("project_id") or [""])[0] or None
                self._send_json({"output_types": list_available_output_types(project_id)})
            elif path == "/api/doi-downloader/status":
                self._send_json(doi_downloader_status())
            elif path == "/api/doi-downloads":
                qs = parse_qs(parsed.query)
                job_id = (qs.get("job_id") or [""])[0] or None
                self._send_json(
                    {
                        "jobs": list_doi_download_jobs(),
                        "items": list_doi_download_items(job_id=job_id),
                    }
                )
            elif path == "/api/maintenance/report":
                self._send_json(maintenance_report())
            elif path == "/api/coverage":
                self._send_json(coverage_detail())
            elif path == "/api/duplicates":
                self._send_json({"duplicates": detect_duplicates()})
            elif path == "/api/chunks":
                qs = parse_qs(parsed.query)
                source_id = (qs.get("source_id") or [""])[0]
                if not source_id:
                    raise JsonError(400, "source_id is required")
                self._send_json({"chunks": source_chunks(source_id)})
            elif path == "/api/source/raw":
                qs = parse_qs(parsed.query)
                source_id = (qs.get("source_id") or [""])[0]
                if not source_id:
                    raise JsonError(400, "source_id is required")
                from .db import connect

                with connect() as con:
                    row = con.execute("SELECT raw_path FROM sources WHERE source_id=?", (source_id,)).fetchone()
                if not row:
                    raise JsonError(404, "source not found")
                self._send_raw_path(Path(row["raw_path"]))
            elif path == "/" or path == "/index.html":
                self._send_file(STATIC_DIR / "index.html")
            elif path == "/viewer" or path == "/pdf-reader":
                self._send_file(STATIC_DIR / "pdf_reader.html")
            elif path.startswith("/static/"):
                requested = (STATIC_DIR / path.removeprefix("/static/")).resolve()
                if STATIC_DIR.resolve() not in requested.parents and requested != STATIC_DIR.resolve():
                    self.send_error(403)
                else:
                    self._send_file(requested)
            elif path.startswith("/vendor/"):
                vendor_dir = config.PROJECT_ROOT / "vendor"
                requested = (vendor_dir / path.removeprefix("/vendor/")).resolve()
                if vendor_dir.resolve() not in requested.parents and requested != vendor_dir.resolve():
                    self.send_error(403)
                else:
                    self._send_file(requested)
            else:
                self.send_error(404)
        except JsonError as exc:
            self._send_json({"status": "failed", "message": exc.message}, exc.status)
        except Exception as exc:
            self._send_json({"status": "failed", "message": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/ingest/upload":
                self._handle_upload()
            elif path == "/api/ingest/folder":
                payload = self._read_json()
                result = ingest_folder(
                    Path(payload.get("folder", "")),
                    domain=payload.get("domain") or "paper",
                    topic=payload.get("topic") or "",
                    sensitivity=payload.get("sensitivity") or "public",
                )
                self._send_json(result)
            elif path == "/api/index/rebuild":
                payload = self._read_json()
                result = rebuild_indexes(
                    index_names=payload.get("index_names") or None,
                    source_id=payload.get("source_id") or None,
                    include_private_api=bool(payload.get("include_private_api")),
                )
                self._send_json(result)
            elif path == "/api/query":
                payload = self._read_json()
                question = (payload.get("question") or "").strip()
                if not question:
                    raise JsonError(400, "question is required")
                self._send_json(
                    answer_query(
                        question,
                        retrieval_mode=payload.get("retrieval_mode") or "all_available",
                        analysis_model=payload.get("analysis_model") or "auto",
                        filters=payload.get("filters") or {},
                        top_k=int(payload.get("top_k") or 10),
                        allow_private_api=bool(payload.get("allow_private_api")),
                    )
                )
            elif path == "/api/compare":
                payload = self._read_json()
                question = (payload.get("question") or "").strip()
                if not question:
                    raise JsonError(400, "question is required")
                self._send_json(compare_modes(question, filters=payload.get("filters") or {}, top_k=int(payload.get("top_k") or 10)))
            elif path == "/api/literature/discover":
                payload = self._read_json()
                query = (payload.get("query") or payload.get("topic") or "").strip()
                if not query:
                    raise JsonError(400, "query is required")
                self._send_json(
                    discover_literature(
                        query=query,
                        keywords=payload.get("keywords") or "",
                        journals=payload.get("journals") or payload.get("journal_constraints") or "",
                        year_from=payload.get("year_from") or None,
                        year_to=payload.get("year_to") or None,
                        max_results=payload.get("max_results") or 10,
                        language_mode=payload.get("language_mode") or "bilingual",
                        translate=bool(payload.get("translate", True)),
                    )
                )
            elif path == "/api/research/projects":
                payload = self._read_json()
                try:
                    result = create_project(
                        name=payload.get("name") or "",
                        description=payload.get("description") or "",
                        pack_id=payload.get("pack_id") or None,
                        default_filters=payload.get("default_filters") or None,
                        metadata=payload.get("metadata") or None,
                    )
                except ValueError as exc:
                    raise JsonError(400, str(exc)) from exc
                self._send_json(result)
            elif path == "/api/research/project/sources/add":
                payload = self._read_json()
                try:
                    result = add_source_to_project(
                        payload.get("project_id") or "",
                        payload.get("source_id") or "",
                        role=payload.get("role") or "reference",
                        tags=payload.get("tags") or [],
                        notes=payload.get("notes") or "",
                        relevance_score=payload.get("relevance_score"),
                    )
                except ValueError as exc:
                    raise JsonError(400, str(exc)) from exc
                self._send_json(result)
            elif path == "/api/research/project/sources/remove":
                payload = self._read_json()
                project_id = payload.get("project_id") or ""
                source_id = payload.get("source_id") or ""
                if not project_id or not source_id:
                    raise JsonError(400, "project_id and source_id are required")
                self._send_json(remove_source_from_project(project_id, source_id))
            elif path == "/api/research/query":
                payload = self._read_json()
                question = (payload.get("question") or "").strip()
                project_id = payload.get("project_id") or ""
                if not project_id:
                    raise JsonError(400, "project_id is required")
                if not question:
                    raise JsonError(400, "question is required")
                try:
                    result = answer_query_for_project(
                        project_id,
                        question,
                        retrieval_mode=payload.get("retrieval_mode") or "all_available",
                        analysis_model=payload.get("analysis_model") or "auto",
                        top_k=int(payload.get("top_k") or 10),
                        allow_private_api=bool(payload.get("allow_private_api")),
                        filters=payload.get("filters") or {},
                    )
                except ValueError as exc:
                    raise JsonError(400, str(exc)) from exc
                self._send_json(result)
            elif path == "/api/research/output/generate":
                payload = self._read_json()
                project_id = payload.get("project_id") or ""
                if not project_id:
                    raise JsonError(400, "project_id is required")
                try:
                    result = generate_project_markdown_output(
                        project_id=project_id,
                        output_type=payload.get("output_type") or "research_summary",
                        question=payload.get("question") or "",
                        title=payload.get("title") or None,
                        retrieval_mode=payload.get("retrieval_mode") or "all_available",
                        top_k=int(payload.get("top_k") or 10),
                        llm_backend=payload.get("llm_backend") or "gemma4",
                    )
                except ValueError as exc:
                    raise JsonError(400, str(exc)) from exc
                self._send_json(result)
            elif path == "/api/outputs/generate":
                payload = self._read_json()
                self._send_json(
                    generate_markdown_output(
                        output_type=payload.get("output_type") or "research_summary",
                        question=payload.get("question") or "",
                        title=payload.get("title") or None,
                        retrieval_mode=payload.get("retrieval_mode") or "all_available",
                        filters=payload.get("filters") or {},
                        top_k=int(payload.get("top_k") or 10),
                        llm_backend=payload.get("llm_backend") or "gemma4",
                    )
                )
            elif path == "/api/source/update":
                payload = self._read_json()
                source_id = payload.get("source_id")
                if not source_id:
                    raise JsonError(400, "source_id is required")
                self._send_json(update_source_metadata(source_id, payload))
            elif path == "/api/backup":
                self._send_json(backup_database())
            elif path == "/api/maintenance/codex-task":
                payload = self._read_json()
                self._send_json(generate_codex_repair_guidance(payload.get("reason") or ""))
            elif path == "/api/retrieval-audits/repair":
                payload = self._read_json()
                audit_id = payload.get("audit_id")
                if not audit_id:
                    raise JsonError(400, "audit_id is required")
                self._send_json(
                    generate_codex_repair_from_audit(
                        audit_id,
                        expected_behavior=payload.get("expected_behavior") or "",
                    )
                )
            elif path == "/api/doi-downloads":
                payload = self._read_json()
                doi_text = payload.get("doi_text") or payload.get("doi") or ""
                if not doi_text.strip():
                    raise JsonError(400, "doi_text is required")
                self._send_json(
                    run_doi_download_job(
                        doi_text,
                        {
                            "out_dir": payload.get("out_dir") or None,
                            "max_items": int(payload.get("max_items") or 10),
                            "headed": bool(payload.get("headed")),
                            "allow_manual_login": bool(payload.get("allow_manual_login")),
                            "manual_login_timeout_seconds": int(payload.get("manual_login_timeout_seconds") or 0)
                            or None,
                            "fast_mode": bool(payload.get("fast_mode")),
                            "auto_ingest": bool(payload.get("auto_ingest")),
                            "rebuild_after_ingest": bool(payload.get("rebuild_after_ingest")),
                        },
                    )
                )
            elif path == "/api/doi-downloader/clear-profile":
                self._send_json(clear_browser_profile())
            else:
                self.send_error(404)
        except JsonError as exc:
            self._send_json({"status": "failed", "message": exc.message}, exc.status)
        except Exception as exc:
            self._send_json({"status": "failed", "message": str(exc)}, 500)

    def _handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise JsonError(400, "multipart/form-data is required")
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )
        file_item = form["file"] if "file" in form else None
        if file_item is None or not getattr(file_item, "filename", None):
            raise JsonError(400, "file is required")
        upload_dir = config.CACHE_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / Path(file_item.filename).name
        with upload_path.open("wb") as fh:
            fh.write(file_item.file.read())
        result = ingest_file(
            upload_path,
            domain=(form.getfirst("domain") or "paper"),
            topic=(form.getfirst("topic") or ""),
            sensitivity=(form.getfirst("sensitivity") or "public"),
        )
        try:
            upload_path.unlink()
        except OSError:
            pass
        self._send_json(result.to_dict())


def main() -> None:
    config.ensure_runtime_dirs()
    init_db()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Personal Knowledge Base Web App: http://{host}:{port}")
    print(f"Project root: {config.PROJECT_ROOT}")
    print(f"Raw data: {config.RAW_DIR}")
    print(f"DB: {config.DB_PATH}")
    print(f"Indexes: {config.INDEX_DIR}")
    server.serve_forever()


if __name__ == "__main__":
    main()
