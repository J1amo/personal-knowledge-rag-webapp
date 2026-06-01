from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_URL = "http://127.0.0.1:8765"


def emit(payload: Any, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_script(*args: str) -> int:
    return subprocess.call([str(PROJECT_ROOT / "scripts" / "webapp.sh"), *args])


def service_health(url: str = DEFAULT_URL) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"status": payload.get("status") or "unknown", "url": url, "error": None}
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {"status": "not_ready", "url": url, "error": str(exc)}


def workflow(_args: argparse.Namespace) -> int:
    emit(
        """Personal Knowledge RAG Webapp 最短路径

已有 PDF：
  ./scripts/pkb.sh ingest /path/to/pdfs --topic "研究方向"
  ./scripts/pkb.sh ask "这个方向的核心问题是什么？"
  ./scripts/pkb.sh markdown "生成带证据的研究摘要" --type research_summary
  ./scripts/pkb.sh chatgpt-packet "让 GPT-5.5 Pro 基于公开论文库形成研究方案"

只有研究方向：
  ./scripts/pkb.sh discover "研究方向" --keywords "关键词1,关键词2" --max-results 8
  ./scripts/pkb.sh open

结果不准或流程异常：
  ./scripts/pkb.sh codex --reason "具体问题"
  ./scripts/pkb.sh codex --audit-id aud_xxx --expected "期望命中的论文/chunk/行为"

每周维护：
  ./scripts/pkb.sh doctor
  ./scripts/pkb.sh status
  ./scripts/pkb.sh logs

隐私边界：默认只使用本地解析、本地索引、本地检索；不要把 private/confidential 文档外发 API。"""
    )
    return 0


def doctor(args: argparse.Namespace) -> int:
    from app import config
    from app.db import init_db
    from app.maintenance import maintenance_report

    config.ensure_runtime_dirs()
    init_db()
    report = maintenance_report()
    checks = {
        "python": sys.executable,
        "python_version": platform.python_version(),
        "project_root": str(PROJECT_ROOT),
        "database": report["database"],
        "coverage": report["coverage"],
        "storage": report["storage"],
        "service": service_health(args.url),
        "system_health": report["system_health"],
        "failed_jobs": report["failed_jobs"],
        "missing_original_files": len(report["missing_original_files"]),
        "api_key_configured": report["api_usage"]["configured"],
        "pymupdf_available": importlib.util.find_spec("fitz") is not None,
        "pytest_available": importlib.util.find_spec("pytest") is not None,
    }
    if args.json:
        emit(checks, as_json=True)
    else:
        lines = [
            "PKB Doctor",
            f"- Project: {PROJECT_ROOT}",
            f"- Python: {checks['python']} ({checks['python_version']})",
            f"- DB: {checks['database']['path']} sources={checks['database']['sources']} documents={checks['database']['documents']}",
            f"- Chunks: {checks['coverage'].get('total_chunks', 0)}",
            f"- System health: {checks['system_health']}",
            f"- Failed jobs: {checks['failed_jobs']}",
            f"- Missing originals: {checks['missing_original_files']}",
            f"- Service: {checks['service']['status']} at {checks['service']['url']}",
            f"- API key configured: {checks['api_key_configured']}",
            f"- PyMuPDF/fitz available: {checks['pymupdf_available']}",
            f"- pytest available: {checks['pytest_available']}",
        ]
        if checks["service"]["error"]:
            lines.append(f"- Service note: {checks['service']['error']}")
        emit("\n".join(lines))
    return 0


def open_app(args: argparse.Namespace) -> int:
    code = run_script("start")
    if code != 0:
        return code
    url = args.url
    if platform.system() == "Darwin":
        subprocess.call(["open", url])
    else:
        emit(f"Open {url}")
    return 0


def ingest(args: argparse.Namespace) -> int:
    from app.ingest import ingest_file, ingest_folder

    target = Path(args.path).expanduser()
    if target.is_dir():
        result = ingest_folder(target, domain=args.domain, topic=args.topic, sensitivity=args.sensitivity)
    else:
        result = ingest_file(target, domain=args.domain, topic=args.topic, sensitivity=args.sensitivity).to_dict()
    emit(result, as_json=args.json)
    return 0 if result.get("status") in {"ready", "duplicate"} else 1


def ask(args: argparse.Namespace) -> int:
    from app.retrieval import answer_query

    result = answer_query(
        args.question,
        retrieval_mode=args.retrieval_mode,
        analysis_model=args.analysis_model,
        filters={"sensitivities": args.sensitivity} if args.sensitivity else {},
        top_k=args.top_k,
        allow_private_api=args.allow_private_api,
    )
    if args.json:
        emit(result, as_json=True)
    else:
        lines = [
            result["analysis"]["answer"],
            "",
            f"audit_id: {result['audit_id']}",
            "evidence:",
        ]
        for item in result["retrieval"]["evidence"]:
            lines.append(
                f"- {item['original_filename']} p.{item.get('page_number') or '?'} "
                f"{item['chunk_id']} found_by={','.join(item.get('found_by', []))}"
            )
        emit("\n".join(lines))
    return 0


def markdown(args: argparse.Namespace) -> int:
    from app.output_studio import generate_markdown_output

    result = generate_markdown_output(
        output_type=args.output_type,
        question=args.question,
        title=args.title,
        retrieval_mode=args.retrieval_mode,
        top_k=args.top_k,
        llm_backend=args.llm_backend,
    )
    if args.json:
        emit(result, as_json=True)
    else:
        emit(f"Markdown output ready: {result['file_path']}")
    return 0


def chatgpt_packet(args: argparse.Namespace) -> int:
    from app.chatgpt_packet import DEFAULT_GOAL, generate_chatgpt_packet

    result = generate_chatgpt_packet(
        goal=args.goal or DEFAULT_GOAL,
        retrieval_mode=args.retrieval_mode,
        top_k=args.top_k,
        output_dir=Path(args.output_dir).expanduser() if args.output_dir else None,
        copy_prompt=not args.no_copy,
    )
    if args.json:
        emit(result, as_json=True)
    elif result.get("status") == "ready":
        clipboard = result.get("clipboard") or {}
        copy_line = (
            "Prompt copied to clipboard."
            if clipboard.get("copied")
            else f"Prompt not copied: {clipboard.get('error') or clipboard.get('status')}"
        )
        emit(
            "\n".join(
                [
                    f"ChatGPT packet ready: {result['zip_path']}",
                    f"Prompt file: {result['prompt_path']}",
                    copy_line,
                    "Original PDFs are not included; upload/copy them manually if needed.",
                ]
            )
        )
    else:
        emit(f"ChatGPT packet not generated: {result.get('reason') or 'unknown reason'}")
    return 0 if result.get("status") == "ready" else 1


def discover(args: argparse.Namespace) -> int:
    from app.literature_discovery import discover_literature

    result = discover_literature(
        query=args.topic,
        keywords=args.keywords,
        journals=args.journals,
        year_from=args.year_from,
        year_to=args.year_to,
        max_results=args.max_results,
        language_mode=args.language_mode,
        translate=not args.no_translate,
    )
    if args.json:
        emit(result, as_json=True)
    else:
        lines = [f"发现候选文献：{result['count']} 条"]
        for item in result["results"]:
            doi = item.get("doi") or "no DOI"
            year = item.get("year") or "unknown year"
            lines.append(f"- {item['title']} ({year}) DOI: {doi}")
        if result.get("warnings"):
            lines.extend(f"warning: {warning}" for warning in result["warnings"])
        emit("\n".join(lines))
    return 0


def codex(args: argparse.Namespace) -> int:
    from app.maintenance import generate_codex_repair_from_audit, generate_codex_repair_guidance

    if args.audit_id:
        result = generate_codex_repair_from_audit(args.audit_id, expected_behavior=args.expected)
    else:
        result = generate_codex_repair_guidance(args.reason)
    emit(result, as_json=args.json)
    return 0 if result.get("status") == "ready" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal Knowledge RAG Webapp unified CLI")
    sub = parser.add_subparsers(dest="command", required=False)

    workflow_parser = sub.add_parser("workflow", help="Show the shortest operating paths")
    workflow_parser.set_defaults(func=workflow)

    doctor_parser = sub.add_parser("doctor", help="Run local health checks")
    doctor_parser.add_argument("--url", default=DEFAULT_URL)
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=doctor)

    open_parser = sub.add_parser("open", help="Start the app and open it in the browser")
    open_parser.add_argument("--url", default=DEFAULT_URL)
    open_parser.set_defaults(func=open_app)

    for name in ("status", "logs"):
        delegated = sub.add_parser(name, help=f"Run scripts/webapp.sh {name}")
        delegated.set_defaults(func=lambda _args, command=name: run_script(command))

    ingest_parser = sub.add_parser("ingest", help="Ingest one PDF or a folder of PDFs")
    ingest_parser.add_argument("path")
    ingest_parser.add_argument("--topic", default="")
    ingest_parser.add_argument("--domain", default="paper")
    ingest_parser.add_argument("--sensitivity", default="public")
    ingest_parser.add_argument("--json", action="store_true")
    ingest_parser.set_defaults(func=ingest)

    ask_parser = sub.add_parser("ask", help="Ask a question against indexed evidence")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--retrieval-mode", default="all_available")
    ask_parser.add_argument("--analysis-model", default="local_llm")
    ask_parser.add_argument("--sensitivity", action="append")
    ask_parser.add_argument("--top-k", type=int, default=10)
    ask_parser.add_argument("--allow-private-api", action="store_true")
    ask_parser.add_argument("--json", action="store_true")
    ask_parser.set_defaults(func=ask)

    markdown_parser = sub.add_parser("markdown", help="Generate a source-grounded Markdown output")
    markdown_parser.add_argument("question")
    markdown_parser.add_argument("--type", dest="output_type", default="research_summary")
    markdown_parser.add_argument("--title")
    markdown_parser.add_argument("--retrieval-mode", default="all_available")
    markdown_parser.add_argument("--top-k", type=int, default=10)
    markdown_parser.add_argument("--llm-backend", default="gemma4")
    markdown_parser.add_argument("--json", action="store_true")
    markdown_parser.set_defaults(func=markdown)

    packet_parser = sub.add_parser("chatgpt-packet", help="Generate a ChatGPT upload zip and copy its prompt")
    packet_parser.add_argument("goal", nargs="?", help="Research goal for GPT-5.5 Pro")
    packet_parser.add_argument("--retrieval-mode", default="strict_exhaustive")
    packet_parser.add_argument("--top-k", type=int, default=40)
    packet_parser.add_argument("--output-dir")
    packet_parser.add_argument("--no-copy", action="store_true", help="Do not copy the prompt to the clipboard")
    packet_parser.add_argument("--json", action="store_true")
    packet_parser.set_defaults(func=chatgpt_packet)

    discover_parser = sub.add_parser("discover", help="Discover candidate papers from OpenAlex")
    discover_parser.add_argument("topic")
    discover_parser.add_argument("--keywords", default="")
    discover_parser.add_argument("--journals", default="")
    discover_parser.add_argument("--year-from")
    discover_parser.add_argument("--year-to")
    discover_parser.add_argument("--max-results", type=int, default=8)
    discover_parser.add_argument("--language-mode", choices=["bilingual", "zh", "en"], default="bilingual")
    discover_parser.add_argument("--no-translate", action="store_true")
    discover_parser.add_argument("--json", action="store_true")
    discover_parser.set_defaults(func=discover)

    codex_parser = sub.add_parser("codex", help="Generate a Codex repair handoff")
    codex_parser.add_argument("--reason", default="")
    codex_parser.add_argument("--audit-id")
    codex_parser.add_argument("--expected", default="")
    codex_parser.add_argument("--json", action="store_true")
    codex_parser.set_defaults(func=codex)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        args = parser.parse_args(["workflow"])
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
