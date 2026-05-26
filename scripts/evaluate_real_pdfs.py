#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


RESEARCH_NAME_HINTS = (
    "arxiv",
    "paper",
    "proceedings",
    "neurips",
    "icml",
    "iclr",
    "acl",
    "emnlp",
    "cvpr",
    "iccv",
    "eccv",
    "sigir",
    "kdd",
    "www",
    "research",
)


def _extract_first_pages(path: Path, max_pages: int = 2) -> str:
    try:
        import fitz  # type: ignore
    except Exception:
        return ""
    try:
        doc = fitz.open(path)
        chunks = []
        for page_idx in range(min(max_pages, doc.page_count)):
            chunks.append(doc.load_page(page_idx).get_text("text"))
        doc.close()
        return "\n".join(chunks)
    except Exception:
        return ""


def _is_research_pdf(path: Path, explicit_dir: bool) -> tuple[bool, str]:
    name = path.name.lower()
    if any(hint in name for hint in RESEARCH_NAME_HINTS):
        return True, "filename_hint"
    text = _extract_first_pages(path).lower()
    if "abstract" in text and ("introduction" in text or "references" in text):
        return True, "abstract_intro_references"
    if explicit_dir and "abstract" in text:
        return True, "explicit_dir_abstract"
    return False, "not_research_like"


def _candidate_dirs(args: argparse.Namespace) -> tuple[list[Path], bool]:
    if args.pdf_dir:
        return [Path(item).expanduser() for item in args.pdf_dir], True
    return [PROJECT_ROOT / "data" / "raw" / "papers"], False


def _find_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    dirs, explicit_dir = _candidate_dirs(args)
    candidates = []
    for folder in dirs:
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*.pdf")):
            if not path.is_file():
                continue
            ok, reason = _is_research_pdf(path, explicit_dir)
            candidates.append({"path": str(path), "research_like": ok, "reason": reason, "bytes": path.stat().st_size})
    return candidates


def _write_report(payload: dict[str, Any], suffix: str) -> Path:
    output_dir = Path(os.getenv("PKB_EVALUATION_OUTPUT_DIR", PROJECT_ROOT / "outputs" / "evaluation"))
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"real_document_evaluation_{suffix}.md"
    lines = [
        "# Real Research PDF Evaluation",
        "",
        f"- status: `{payload['status']}`",
        f"- candidate_count: {payload.get('candidate_count', 0)}",
        f"- selected_count: {len(payload.get('selected', []))}",
        "",
        "## Details",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _configure_isolated_runtime() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory(prefix="pkb-real-pdf-eval-")
    root = Path(tmp.name)
    os.environ["PKB_DATA_DIR"] = str(root / "data")
    os.environ["PKB_DB_DIR"] = str(root / "db")
    os.environ["PKB_INDEX_DIR"] = str(root / "indexes")
    os.environ["PKB_CACHE_DIR"] = str(root / "cache")
    os.environ["PKB_BACKUP_DIR"] = str(root / "backups")
    os.environ["PKB_OUTPUT_DIR"] = str(root / "outputs")
    return tmp


def run_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    candidates = _find_candidates(args)
    selected = [item for item in candidates if item["research_like"]][: args.limit]
    if len(selected) < 3:
        payload = {
            "status": "blocked",
            "reason": "Need 3-5 real research PDFs. Default scan only checks project data/raw/papers to avoid touching private home documents.",
            "candidate_count": len(candidates),
            "selected": selected,
            "candidates": candidates,
            "hint": "Run scripts/evaluate_real_pdfs.py --pdf-dir /path/to/research-pdfs after placing 3-5 public papers there.",
        }
        payload["report_path"] = str(_write_report(payload, "blocked"))
        return payload

    runtime = _configure_isolated_runtime()
    try:
        from app import config
        from app.db import init_db
        from app.indexes import rebuild_indexes
        from app.ingest import ingest_file
        from app.retrieval import answer_query, compare_modes

        init_db()
        ingested = []
        for item in selected:
            result = ingest_file(Path(item["path"]), domain="paper", topic="real_eval", sensitivity="public")
            ingested.append(result.to_dict())
        rebuild = rebuild_indexes(index_names=[config.LOCAL_VECTOR_INDEX, config.BM25_INDEX, config.GRAPH_INDEX])
        queries = [
            "What is the main problem and contribution across these papers?",
            "Which methods or system components are proposed?",
            "What limitations, evaluation gaps, or future work are mentioned?",
        ]
        answers = [
            answer_query(
                query,
                retrieval_mode="all_available",
                analysis_model="local_llm",
                filters={"domains": ["paper"], "sensitivities": ["public"]},
                top_k=8,
            )
            for query in queries
        ]
        compare = compare_modes(
            queries[0],
            filters={"domains": ["paper"], "sensitivities": ["public"]},
            top_k=8,
        )
        payload = {
            "status": "ready",
            "runtime": "isolated_temp_db",
            "candidate_count": len(candidates),
            "selected": selected,
            "ingested": ingested,
            "rebuild": rebuild,
            "answers": [
                {
                    "query": item["question"],
                    "audit_id": item["audit_id"],
                    "evidence_count": len(item["retrieval"]["evidence"]),
                    "citations": item["citations"],
                    "warnings": item["retrieval"].get("errors", []),
                }
                for item in answers
            ],
            "compare": compare,
        }
        payload["report_path"] = str(_write_report(payload, "ready"))
        return payload
    finally:
        runtime.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval with 3-5 real public research PDFs.")
    parser.add_argument("--pdf-dir", action="append", help="Directory containing public research PDFs. Can be repeated.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum PDFs to evaluate.")
    args = parser.parse_args()
    payload = run_evaluation(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
