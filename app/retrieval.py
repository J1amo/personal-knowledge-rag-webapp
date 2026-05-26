from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from typing import Any

from . import config
from .analysis import generate_answer
from .db import connect, json_dumps, utc_now
from .indexes import (
    fetch_metadata,
    search_api_vector,
    search_bm25,
    search_full_scan,
    search_graph,
    search_local_vector,
)

RETRIEVAL_MODES = {
    "fast_local": "Fast Local",
    "api_only": "API Only",
    "all_available": "All Available Indexes",
    "private_local_only": "Private Local Only",
    "strict_exhaustive": "Strict Exhaustive",
}

ANALYSIS_MODELS = {
    "local_llm": "Local LLM / Extractive",
    "api_llm": "API LLM",
    "auto": "Auto",
}


def _normalize_filters(filters: dict[str, Any] | None, retrieval_mode: str) -> dict[str, Any]:
    filters = dict(filters or {})
    for key in ("domains", "topics", "source_ids", "sensitivities"):
        value = filters.get(key)
        if isinstance(value, str):
            filters[key] = [value] if value else []
        elif value is None:
            filters[key] = []
    if retrieval_mode in {"all_available", "api_only", "strict_exhaustive"} and not filters["sensitivities"]:
        filters["sensitivities"] = ["public"]
    return filters


def _private_scope(filters: dict[str, Any]) -> bool:
    sensitivities = set(filters.get("sensitivities") or [])
    return bool(sensitivities & config.PRIVATE_SENSITIVITIES)


def _merge_groups(groups: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    merged: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for backend, hits in groups:
        for hit in hits:
            key = hit.get("chunk_id") or f"{hit.get('source_id')}:{hit.get('page_number')}:{hit.get('text_hash')}"
            if key not in merged:
                merged[key] = {
                    "chunk_id": hit.get("chunk_id"),
                    "source_id": hit.get("source_id"),
                    "found_by": [],
                    "ranks": {},
                    "scores": {},
                    "rrf_score": 0.0,
                    "merged_backend_count": 0,
                }
            item = merged[key]
            if backend not in item["found_by"]:
                item["found_by"].append(backend)
                item["merged_backend_count"] = len(item["found_by"])
            rank = int(hit.get("rank") or 999)
            item["ranks"][backend] = rank
            item["scores"][backend] = float(hit.get("score") or 0)
            item["rrf_score"] += 1.0 / (60 + rank)
    results = list(merged.values())
    results.sort(key=lambda item: item["rrf_score"], reverse=True)
    for idx, item in enumerate(results, start=1):
        item["final_rank"] = idx
    return results


def _attach_metadata(merged: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    selected = merged[:top_k]
    metadata = fetch_metadata([item["chunk_id"] for item in selected if item.get("chunk_id")])
    evidence = []
    for item in selected:
        meta = metadata.get(item["chunk_id"])
        if not meta:
            continue
        evidence.append({**item, **meta})
    return evidence


def _expand_adjacent(evidence: list[dict[str, Any]], filters: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    if not evidence:
        return evidence
    source_ids = list({item["source_id"] for item in evidence})
    with connect() as con:
        additions = []
        for item in evidence[:limit]:
            row = con.execute("SELECT chunk_index FROM chunks WHERE chunk_id=?", (item["chunk_id"],)).fetchone()
            if not row:
                continue
            for offset in (-1, 1):
                neighbor = con.execute(
                    """
                    SELECT ch.chunk_id, ch.source_id, ch.text, ch.text_hash, ch.page_number,
                           ch.section_title, ch.timestamp_start, ch.timestamp_end,
                           s.original_filename, s.raw_path, s.domain, s.topic, s.sensitivity
                    FROM chunks ch JOIN sources s ON s.source_id = ch.source_id
                    WHERE ch.source_id=? AND ch.chunk_index=?
                    """,
                    (item["source_id"], row["chunk_index"] + offset),
                ).fetchone()
                if neighbor:
                    additions.append(dict(neighbor))
    existing = {item["chunk_id"] for item in evidence}
    for addition in additions:
        if addition["chunk_id"] in existing:
            continue
        addition.update(
            {
                "found_by": ["adjacent_expansion"],
                "ranks": {"adjacent_expansion": 999},
                "scores": {"adjacent_expansion": 0.0},
                "rrf_score": 0.0,
                "final_rank": len(evidence) + 1,
            }
        )
        evidence.append(addition)
        existing.add(addition["chunk_id"])
    return evidence


def retrieve(
    question: str,
    *,
    retrieval_mode: str = "all_available",
    filters: dict[str, Any] | None = None,
    top_k: int = 10,
    allow_private_api: bool = False,
) -> dict[str, Any]:
    filters = _normalize_filters(filters, retrieval_mode)
    private_scope = _private_scope(filters)
    api_allowed = allow_private_api or not private_scope
    errors: list[str] = []
    groups: list[tuple[str, list[dict[str, Any]]]] = []

    if retrieval_mode in {"fast_local", "all_available", "private_local_only", "strict_exhaustive"}:
        groups.append(("local_vector", search_local_vector(question, filters, top_k=top_k * 2)))
        groups.append(("bm25", search_bm25(question, filters, top_k=top_k * 2)))
        groups.append(("graph", search_graph(question, filters, top_k=top_k * 2)))

    if retrieval_mode in {"api_only", "all_available", "strict_exhaustive"}:
        if api_allowed and retrieval_mode != "private_local_only":
            api_hits, error = search_api_vector(
                question, filters, top_k=top_k * 2, allow_private_api=allow_private_api
            )
            groups.append(("api_vector", api_hits))
            if error:
                errors.append(error)
        else:
            errors.append("API retrieval skipped by privacy policy")

    if retrieval_mode == "strict_exhaustive":
        groups.append(("full_scan", search_full_scan(question, filters, top_k=top_k * 3)))

    merged = _merge_groups(groups)
    evidence = _attach_metadata(merged, top_k)
    if retrieval_mode == "strict_exhaustive":
        evidence = _expand_adjacent(evidence, filters)
    return {
        "retrieval_mode": retrieval_mode,
        "filters": filters,
        "private_scope": private_scope,
        "api_retrieval_allowed": api_allowed and retrieval_mode not in {"fast_local", "private_local_only"},
        "errors": errors,
        "raw_result_counts": {backend: len(hits) for backend, hits in groups},
        "backend_results": {
            backend: [
                {
                    "chunk_id": hit.get("chunk_id"),
                    "source_id": hit.get("source_id"),
                    "rank": hit.get("rank"),
                    "score": hit.get("score"),
                }
                for hit in hits[:top_k]
            ]
            for backend, hits in groups
        },
        "merged_count": len(merged),
        "dropped_duplicates": [
            {
                "chunk_id": item.get("chunk_id"),
                "source_id": item.get("source_id"),
                "found_by": item.get("found_by", []),
            }
            for item in merged
            if len(item.get("found_by", [])) > 1
        ],
        "evidence": evidence,
    }


def _ensure_citations_and_audit(
    *,
    audit_id: str,
    question: str,
    retrieval_mode: str,
    retrieval: dict[str, Any],
    latency_ms: int,
) -> list[dict[str, Any]]:
    citations = []
    result_rows = []
    created_at = utc_now()
    with connect() as con:
        for item in retrieval["evidence"]:
            doc = con.execute(
                "SELECT document_id FROM documents WHERE primary_source_id=?",
                (item["source_id"],),
            ).fetchone()
            document_id = doc["document_id"] if doc else None
            citation_id = f"cit_{item['chunk_id']}"
            quote = " ".join((item.get("text") or "").split())[:700]
            con.execute(
                """
                INSERT INTO citations (
                  citation_id, chunk_id, source_id, document_id, source_file,
                  page_number, section_title, text_hash, quote, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(citation_id) DO UPDATE SET
                  source_file=excluded.source_file,
                  page_number=excluded.page_number,
                  section_title=excluded.section_title,
                  text_hash=excluded.text_hash,
                  quote=excluded.quote
                """,
                (
                    citation_id,
                    item["chunk_id"],
                    item["source_id"],
                    document_id,
                    item["original_filename"],
                    item.get("page_number"),
                    item.get("section_title"),
                    item.get("text_hash"),
                    quote,
                    created_at,
                ),
            )
            item["citation_id"] = citation_id
            citation = {
                "citation_id": citation_id,
                "chunk_id": item["chunk_id"],
                "source_id": item["source_id"],
                "source_file": item["original_filename"],
                "page_number": item.get("page_number"),
                "found_by": item.get("found_by", []),
            }
            citations.append(citation)
            result_rows.append(
                (
                    f"res_{audit_id}_{item['final_rank']:03d}",
                    audit_id,
                    item["chunk_id"],
                    item["source_id"],
                    item["final_rank"],
                    json_dumps(item.get("found_by", [])),
                    json_dumps(item.get("ranks", {})),
                    json_dumps(item.get("scores", {})),
                    citation_id,
                    int(len(item.get("found_by", [])) > 1),
                    quote,
                    created_at,
                )
            )
        warning_flags = []
        if retrieval.get("errors"):
            warning_flags.extend(retrieval["errors"])
        if not retrieval["evidence"]:
            warning_flags.append("no_evidence_found")
        con.execute(
            """
            INSERT INTO retrieval_audits (
              audit_id, query_text, retrieval_mode, created_at, selected_corpus_json,
              backends_used_json, backend_results_json, merged_results_json,
              dropped_duplicates_json, answer_citations_json, warning_flags_json,
              skipped_json, latency_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                question,
                retrieval_mode,
                created_at,
                json_dumps(retrieval.get("filters", {})),
                json_dumps(list(retrieval.get("raw_result_counts", {}).keys())),
                json_dumps(retrieval.get("backend_results", {})),
                json_dumps(
                    [
                        {
                            "chunk_id": item.get("chunk_id"),
                            "source_id": item.get("source_id"),
                            "final_rank": item.get("final_rank"),
                            "found_by": item.get("found_by", []),
                        }
                        for item in retrieval["evidence"]
                    ]
                ),
                json_dumps(retrieval.get("dropped_duplicates", [])),
                json_dumps(citations),
                json_dumps(warning_flags),
                json_dumps(retrieval.get("errors", [])),
                latency_ms,
            ),
        )
        con.executemany(
            """
            INSERT INTO retrieval_results (
              result_id, audit_id, chunk_id, source_id, final_rank,
              found_by_json, ranks_json, scores_json, citation_id,
              duplicate_merged, snippet, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            result_rows,
        )
    return citations


def answer_query(
    question: str,
    *,
    retrieval_mode: str = "all_available",
    analysis_model: str = "auto",
    filters: dict[str, Any] | None = None,
    top_k: int = 10,
    allow_private_api: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    retrieval = retrieve(
        question,
        retrieval_mode=retrieval_mode,
        filters=filters,
        top_k=top_k,
        allow_private_api=allow_private_api,
    )
    analysis = generate_answer(
        question,
        retrieval["evidence"],
        analysis_model,
        allow_private_api=allow_private_api,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    query_id = "qry_" + uuid.uuid4().hex
    audit_id = "aud_" + uuid.uuid4().hex
    citations = _ensure_citations_and_audit(
        audit_id=audit_id,
        question=question,
        retrieval_mode=retrieval_mode,
        retrieval=retrieval,
        latency_ms=latency_ms,
    )
    with connect() as con:
        con.execute(
            """
            INSERT INTO query_logs (
              query_id, question, retrieval_mode, analysis_model, filters_json,
              retrieved_chunk_ids, final_answer, created_at, latency_ms, api_cost
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                query_id,
                question,
                retrieval_mode,
                analysis_model,
                json_dumps(retrieval["filters"]),
                json_dumps([item["chunk_id"] for item in retrieval["evidence"]]),
                analysis["answer"],
                utc_now(),
                latency_ms,
            ),
        )
    return {
        "query_id": query_id,
        "audit_id": audit_id,
        "question": question,
        "retrieval": retrieval,
        "analysis": analysis,
        "citations": citations,
        "latency_ms": latency_ms,
    }


def compare_modes(question: str, filters: dict[str, Any] | None = None, top_k: int = 10) -> dict[str, Any]:
    modes = ["fast_local", "api_only", "all_available", "strict_exhaustive"]
    runs = {}
    for mode in modes:
        start = time.monotonic()
        result = retrieve(question, retrieval_mode=mode, filters=filters, top_k=top_k)
        chunk_ids = [item["chunk_id"] for item in result["evidence"]]
        source_ids = sorted({item["source_id"] for item in result["evidence"]})
        runs[mode] = {
            "latency_ms": int((time.monotonic() - start) * 1000),
            "chunk_ids": chunk_ids,
            "source_ids": source_ids,
            "raw_result_counts": result["raw_result_counts"],
            "errors": result["errors"],
        }
    all_ids = {mode: set(run["chunk_ids"]) for mode, run in runs.items()}
    overlap = {}
    missing = {}
    for left in modes:
        for right in modes:
            if left == right:
                continue
            overlap[f"{left}_vs_{right}"] = len(all_ids[left] & all_ids[right])
            missing[f"{left}_missing_from_{right}"] = sorted(all_ids[right] - all_ids[left])
    return {"question": question, "runs": runs, "overlap": overlap, "missing": missing}
