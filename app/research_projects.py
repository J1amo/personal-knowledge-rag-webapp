from __future__ import annotations

import re
import uuid
from typing import Any

from .db import connect, init_db, json_dumps, json_loads, utc_now
from .retrieval import answer_query, retrieve

EMPTY_PROJECT_SOURCE_ID = "__no_project_sources__"
DEFAULT_PROJECT_FILTERS = {"domains": ["paper"], "sensitivities": ["public"]}


def _project_id_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    slug = slug[:48] or uuid.uuid4().hex[:12]
    return f"proj_{slug}"


def _unique_project_id(name: str) -> str:
    base = _project_id_from_name(name)
    with connect() as con:
        existing = con.execute("SELECT 1 FROM research_projects WHERE project_id=?", (base,)).fetchone()
    if not existing:
        return base
    return f"{base}_{uuid.uuid4().hex[:8]}"


def create_project(
    *,
    name: str,
    description: str = "",
    pack_id: str | None = None,
    default_filters: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    init_db()
    name = (name or "").strip()
    if not name:
        raise ValueError("Project name is required")
    now = utc_now()
    project_id = project_id or _unique_project_id(name)
    with connect() as con:
        con.execute(
            """
            INSERT INTO research_projects (
              project_id, name, description, pack_id, default_filters_json,
              privacy_policy, status, created_at, updated_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                description,
                pack_id or None,
                json_dumps(default_filters or DEFAULT_PROJECT_FILTERS),
                "inherit_source",
                "active",
                now,
                now,
                json_dumps(metadata or {}),
            ),
        )
    project = get_project(project_id)
    if not project:
        raise RuntimeError("Project was not created")
    return project


def _project_from_row(row: Any) -> dict[str, Any]:
    project = dict(row)
    project["default_filters"] = json_loads(project.pop("default_filters_json"), {})
    project["metadata"] = json_loads(project.pop("metadata_json"), {})
    return project


def list_projects() -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM project_sources ps WHERE ps.project_id=p.project_id) AS source_count
            FROM research_projects p
            ORDER BY updated_at DESC, created_at DESC
            """
        ).fetchall()
    projects = []
    for row in rows:
        project = _project_from_row(row)
        project["source_count"] = row["source_count"]
        projects.append(project)
    return projects


def get_project(project_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as con:
        row = con.execute("SELECT * FROM research_projects WHERE project_id=?", (project_id,)).fetchone()
    return _project_from_row(row) if row else None


def add_source_to_project(
    project_id: str,
    source_id: str,
    *,
    role: str = "reference",
    tags: list[str] | None = None,
    notes: str = "",
    relevance_score: float | None = None,
) -> dict[str, Any]:
    init_db()
    if not get_project(project_id):
        raise ValueError("Project not found")
    now = utc_now()
    with connect() as con:
        source = con.execute("SELECT source_id FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if not source:
            raise ValueError("Source not found")
        con.execute(
            """
            INSERT INTO project_sources (
              project_id, source_id, role, tags_json, relevance_score, notes, added_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, source_id) DO UPDATE SET
              role=excluded.role,
              tags_json=excluded.tags_json,
              relevance_score=excluded.relevance_score,
              notes=excluded.notes
            """,
            (
                project_id,
                source_id,
                role or "reference",
                json_dumps(tags or []),
                relevance_score,
                notes,
                now,
            ),
        )
        con.execute(
            "UPDATE research_projects SET updated_at=? WHERE project_id=?",
            (now, project_id),
        )
    return {"status": "ready", "project_id": project_id, "source_id": source_id}


def remove_source_from_project(project_id: str, source_id: str) -> dict[str, Any]:
    init_db()
    now = utc_now()
    with connect() as con:
        con.execute(
            "DELETE FROM project_sources WHERE project_id=? AND source_id=?",
            (project_id, source_id),
        )
        con.execute("UPDATE research_projects SET updated_at=? WHERE project_id=?", (now, project_id))
    return {"status": "ready", "project_id": project_id, "source_id": source_id}


def list_project_sources(project_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT ps.project_id, ps.source_id, ps.role, ps.tags_json,
                   ps.relevance_score, ps.notes, ps.added_at,
                   s.original_filename, s.domain, s.topic, s.sensitivity,
                   s.raw_path, s.file_hash, s.ingestion_status,
                   (SELECT COUNT(*) FROM chunks ch WHERE ch.source_id=s.source_id) AS chunk_count
            FROM project_sources ps
            JOIN sources s ON s.source_id=ps.source_id
            WHERE ps.project_id=?
            ORDER BY ps.added_at DESC
            """,
            (project_id,),
        ).fetchall()
    sources = []
    for row in rows:
        item = dict(row)
        item["tags"] = json_loads(item.pop("tags_json"), [])
        sources.append(item)
    return sources


def list_project_source_ids(project_id: str) -> list[str]:
    init_db()
    with connect() as con:
        rows = con.execute(
            "SELECT source_id FROM project_sources WHERE project_id=? ORDER BY added_at DESC",
            (project_id,),
        ).fetchall()
    return [row["source_id"] for row in rows]


def resolve_project_filters(project_id: str, extra_filters: dict[str, Any] | None = None) -> dict[str, Any]:
    project = get_project(project_id)
    if not project:
        raise ValueError("Project not found")
    filters = dict(project.get("default_filters") or {})
    filters.update(extra_filters or {})
    source_ids = list_project_source_ids(project_id)
    filters["source_ids"] = source_ids or [EMPTY_PROJECT_SOURCE_ID]
    return filters


def retrieve_for_project(
    project_id: str,
    question: str,
    *,
    retrieval_mode: str = "all_available",
    top_k: int = 10,
    allow_private_api: bool = False,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return retrieve(
        question,
        retrieval_mode=retrieval_mode,
        filters=resolve_project_filters(project_id, filters),
        top_k=top_k,
        allow_private_api=allow_private_api,
    )


def answer_query_for_project(
    project_id: str,
    question: str,
    *,
    retrieval_mode: str = "all_available",
    analysis_model: str = "auto",
    top_k: int = 10,
    allow_private_api: bool = False,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return answer_query(
        question,
        retrieval_mode=retrieval_mode,
        analysis_model=analysis_model,
        filters=resolve_project_filters(project_id, filters),
        top_k=top_k,
        allow_private_api=allow_private_api,
    )
