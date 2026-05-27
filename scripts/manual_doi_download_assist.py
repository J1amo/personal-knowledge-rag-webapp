#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Any

preferred_python = os.getenv("PKB_PYTHON") or os.getenv("PYTHON")
if preferred_python:
    preferred_path = Path(preferred_python).expanduser()
    if preferred_path.exists() and Path(sys.executable).resolve() != preferred_path.resolve():
        os.execv(str(preferred_path), [str(preferred_path), *sys.argv])

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.doi_downloader import (  # noqa: E402
    LOG_DIR,
    _batch_count,
    _create_item,
    _create_job,
    _db_existing_download,
    _finish_job,
    _ingest_downloaded_pdf,
    _record_metadata,
    _update_item,
    default_out_dir,
    dedupe_dois,
    fetch_crossref_metadata,
    find_existing_download,
    parse_doi_list,
    publisher_domain,
    resolve_settings,
    save_pdf_and_metadata,
)


def _existing_pdfs(directories: list[Path]) -> set[Path]:
    paths: set[Path] = set()
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.glob("*.pdf"):
            try:
                paths.add(path.resolve())
            except OSError:
                continue
    return paths


def _is_stable_pdf(path: Path, stable_seconds: float) -> bool:
    try:
        first = path.stat()
        if first.st_size <= 0:
            return False
        time.sleep(stable_seconds)
        second = path.stat()
    except OSError:
        return False
    return first.st_size == second.st_size and second.st_size > 0


def _new_pdf_candidates(directories: list[Path], seen: set[Path], since: float) -> list[Path]:
    candidates: list[Path] = []
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.glob("*.pdf"):
            try:
                resolved = path.resolve()
                stat = path.stat()
            except OSError:
                continue
            if resolved in seen or stat.st_mtime < since:
                continue
            if path.name.startswith(".") or path.suffix.lower() != ".pdf":
                continue
            candidates.append(path)
    return sorted(candidates, key=lambda item: item.stat().st_mtime)


def wait_for_manual_pdf(
    watch_dirs: list[Path],
    seen: set[Path],
    *,
    timeout_seconds: int,
    poll_seconds: float,
    stable_seconds: float,
) -> Path | None:
    start = time.time()
    deadline = start + timeout_seconds
    while time.time() < deadline:
        for candidate in _new_pdf_candidates(watch_dirs, seen, start):
            if _is_stable_pdf(candidate, stable_seconds):
                return candidate
        time.sleep(poll_seconds)
    return None


def _read_inputs(args: argparse.Namespace) -> list[str]:
    parts = []
    if args.doi:
        parts.extend(args.doi)
    if args.doi_file:
        parts.append(Path(args.doi_file).expanduser().read_text(encoding="utf-8"))
    if not parts:
        raise SystemExit("Provide --doi or --doi-file")
    return dedupe_dois(parse_doi_list("\n".join(parts)))


def _watch_dirs(args: argparse.Namespace, out_dir: Path) -> list[Path]:
    directories = [Path(item).expanduser() for item in args.watch_dir]
    if not directories:
        directories = [Path.home() / "Downloads", out_dir]
    result: list[Path] = []
    for directory in directories:
        if directory not in result:
            result.append(directory)
    return result


def _write_manual_log(job_id: str, summary: dict[str, Any], items: list[dict[str, Any]]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{job_id}.manual.json"
    path.write_text(json.dumps({"job_id": job_id, "summary": summary, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_manual_assist(args: argparse.Namespace) -> dict[str, Any]:
    dois = _read_inputs(args)
    out_dir = Path(args.out or default_out_dir()).expanduser()
    settings = resolve_settings(
        {
            "out_dir": str(out_dir),
            "max_items": args.max_items,
            "auto_ingest": args.auto_ingest,
            "rebuild_after_ingest": args.rebuild_after_ingest,
        }
    )
    job_id = _create_job(dois, settings, len(dois))
    watch_dirs = _watch_dirs(args, out_dir)
    items: list[dict[str, Any]] = []
    stop_reason = None

    for idx, doi in enumerate(dois, start=1):
        batch_index = (idx - 1) // settings.max_items + 1
        batch_item_index = (idx - 1) % settings.max_items + 1
        item_id = _create_item(job_id, doi)
        landing_url = f"https://doi.org/{doi}"
        metadata = {"doi": doi, **fetch_crossref_metadata(doi)}
        _record_metadata(doi, metadata)

        existing = find_existing_download(doi, out_dir) or _db_existing_download(doi)
        if existing:
            _update_item(
                item_id,
                status="skipped_existing",
                landing_url=landing_url,
                saved_path=existing.get("saved_path"),
                metadata_path=existing.get("metadata_path"),
                file_hash=existing.get("file_hash"),
            )
            items.append(
                {
                    "id": item_id,
                    "doi": doi,
                    "status": "skipped_existing",
                    "landing_url": landing_url,
                    "batch_index": batch_index,
                    "batch_item_index": batch_item_index,
                    **existing,
                }
            )
            continue

        seen = _existing_pdfs(watch_dirs)
        _update_item(item_id, status="manual_wait", landing_url=landing_url)
        print(f"\n[{idx}/{len(dois)}] Opened {landing_url}", flush=True)
        print("请在浏览器里完成登录/验证并下载当前 DOI 的 PDF。脚本会自动侦测新 PDF。", flush=True)
        webbrowser.open(landing_url, new=2)

        pdf_path = wait_for_manual_pdf(
            watch_dirs,
            seen,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            stable_seconds=args.stable_seconds,
        )
        if not pdf_path:
            stop_reason = f"No manual PDF downloaded for {doi} within {args.timeout_seconds} seconds"
            _update_item(item_id, status="failed", landing_url=landing_url, failure_reason=stop_reason)
            items.append(
                {
                    "id": item_id,
                    "doi": doi,
                    "status": "failed",
                    "landing_url": landing_url,
                    "failure_reason": stop_reason,
                    "batch_index": batch_index,
                    "batch_item_index": batch_item_index,
                }
            )
            break

        try:
            saved = save_pdf_and_metadata(
                doi=doi,
                pdf_bytes=pdf_path.read_bytes(),
                out_dir=out_dir,
                metadata=metadata,
                landing_url=landing_url,
                pdf_url=None,
                domain=publisher_domain(landing_url),
            )
            ingestion_source_id = None
            if settings.auto_ingest:
                ingest_result = _ingest_downloaded_pdf(
                    saved["saved_path"], doi, saved["metadata"], settings.rebuild_after_ingest
                )
                ingestion_source_id = ingest_result.get("source_id")
            _update_item(
                item_id,
                status="downloaded",
                landing_url=landing_url,
                publisher_domain=publisher_domain(landing_url),
                saved_path=saved["saved_path"],
                metadata_path=saved["metadata_path"],
                file_hash=saved["file_hash"],
                ingestion_source_id=ingestion_source_id,
            )
            items.append(
                {
                    "id": item_id,
                    "doi": doi,
                    "status": "downloaded",
                    "landing_url": landing_url,
                    "manual_source_path": str(pdf_path),
                    "saved_path": saved["saved_path"],
                    "metadata_path": saved["metadata_path"],
                    "file_hash": saved["file_hash"],
                    "ingestion_source_id": ingestion_source_id,
                    "batch_index": batch_index,
                    "batch_item_index": batch_item_index,
                }
            )
        except Exception as exc:
            stop_reason = str(exc)
            _update_item(item_id, status="failed", landing_url=landing_url, failure_reason=stop_reason)
            items.append(
                {
                    "id": item_id,
                    "doi": doi,
                    "status": "failed",
                    "landing_url": landing_url,
                    "manual_source_path": str(pdf_path),
                    "failure_reason": stop_reason,
                    "batch_index": batch_index,
                    "batch_item_index": batch_item_index,
                }
            )
            break

    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    if stop_reason:
        status = "stopped"
    elif counts and all(state in {"downloaded", "skipped_existing"} for state in counts):
        status = "ready"
    elif counts.get("downloaded") or counts.get("skipped_existing"):
        status = "partial"
    else:
        status = "failed"

    summary = {
        "status_counts": counts,
        "input_count": len(dois),
        "requested_count": len(dois),
        "processed_count": len(items),
        "unprocessed_count": max(0, len(dois) - len(items)),
        "batch_size": settings.max_items,
        "batch_count": _batch_count(len(dois), settings.max_items),
        "completed_batches": _batch_count(len(items), settings.max_items),
        "stopped_reason": stop_reason,
        "manual_browser_assist": True,
        "watch_dirs": [str(path) for path in watch_dirs],
        "out_dir": str(out_dir),
        "settings": asdict(settings),
    }
    log_path = _write_manual_log(job_id, summary, items)
    summary["log_path"] = str(log_path)
    _finish_job(job_id, status, summary, stop_reason)
    return {"status": status, "job_id": job_id, "summary": summary, "items": items}


def main() -> int:
    parser = argparse.ArgumentParser(description="Assist manual DOI PDF downloads through the user's real browser.")
    parser.add_argument("--doi", action="append", help="Single DOI. Can be repeated.")
    parser.add_argument("--doi-file", help="Text file containing DOI values.")
    parser.add_argument("--out", help="Output directory. Defaults to data/raw/papers.")
    parser.add_argument("--watch-dir", action="append", default=[], help="Directory to watch for newly downloaded PDFs. Can be repeated.")
    parser.add_argument("--max-items", type=int, default=20, help="Batch size metadata for the job log.")
    parser.add_argument("--timeout-seconds", type=int, default=3600, help="Seconds to wait for each manually downloaded PDF.")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Polling interval for new PDFs.")
    parser.add_argument("--stable-seconds", type=float, default=2.0, help="Seconds a PDF size must remain stable before ingest.")
    parser.add_argument("--auto-ingest", action="store_true", help="Ingest downloaded PDFs into the knowledge base.")
    parser.add_argument("--rebuild-after-ingest", action="store_true", help="Rebuild indexes after optional ingestion.")
    args = parser.parse_args()
    result = run_manual_assist(args)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result.get("status") in {"ready", "partial", "stopped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
