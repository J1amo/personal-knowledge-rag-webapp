#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

preferred_python = os.getenv("PKB_PYTHON") or os.getenv("PYTHON")
if preferred_python:
    preferred_path = Path(preferred_python).expanduser()
    if preferred_path.exists() and Path(sys.executable).resolve() != preferred_path.resolve():
        os.execv(str(preferred_path), [str(preferred_path), *sys.argv])

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.doi_downloader import run_doi_download_job, run_doi_verification_queue_job  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Download explicitly supplied DOI PDFs with authorized access.")
    parser.add_argument("--doi", action="append", help="Single DOI. Can be repeated.")
    parser.add_argument("--doi-file", help="Text file containing DOI values.")
    parser.add_argument("--out", help="Output directory. Defaults to data/raw/papers.")
    parser.add_argument("--max-items", type=int, default=10, help="Maximum DOI values per batch. The job still processes the full deduped list.")
    parser.add_argument("--headed", action="store_true", help="Debug only: show the automation browser for the whole run.")
    parser.add_argument(
        "--allow-manual-login",
        action="store_true",
        help="Allow manual waiting on login or access pages when --headed is also enabled.",
    )
    parser.add_argument(
        "--campus-session-mode",
        action="store_true",
        help=(
            "Use a visible persistent browser session for authorized campus/library access. "
            "Implies --headed and --allow-manual-login and lets the user clear CAPTCHA/security pages manually."
        ),
    )
    parser.add_argument(
        "--manual-login-timeout-seconds",
        type=int,
        default=None,
        help="Seconds to wait on login or access pages when visible manual waiting is enabled.",
    )
    parser.add_argument("--fast-mode", action="store_true", help="Use 5-10s article delay, max 5 DOI values per batch.")
    parser.add_argument("--auto-ingest", action="store_true", help="Ingest downloaded PDFs into the knowledge base.")
    parser.add_argument("--rebuild-after-ingest", action="store_true", help="Rebuild indexes after optional ingestion.")
    parser.add_argument("--no-deepseek", action="store_true", help="Disable DeepSeek page advice even when DEEPSEEK_API_KEY is set.")
    parser.add_argument(
        "--retry-verification-queue",
        action="store_true",
        help="Retry the latest DOI values waiting for login or access verification, reusing the persistent access session.",
    )
    args = parser.parse_args()

    parts = []
    if args.doi:
        parts.extend(args.doi)
    if args.doi_file:
        parts.append(Path(args.doi_file).expanduser().read_text(encoding="utf-8"))
    if not parts and not args.retry_verification_queue:
        parser.error("Provide --doi or --doi-file")

    settings = {
        "out_dir": args.out,
        "max_items": args.max_items,
        "headed": args.headed,
        "allow_manual_login": args.allow_manual_login,
        "campus_session_mode": args.campus_session_mode,
        "manual_login_timeout_seconds": args.manual_login_timeout_seconds,
        "fast_mode": args.fast_mode,
        "auto_ingest": args.auto_ingest,
        "rebuild_after_ingest": args.rebuild_after_ingest,
        "use_deepseek": not args.no_deepseek,
    }
    if args.retry_verification_queue:
        result = run_doi_verification_queue_job(settings)
    else:
        result = run_doi_download_job("\n".join(parts), settings)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ready", "partial", "stopped", "noop"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
