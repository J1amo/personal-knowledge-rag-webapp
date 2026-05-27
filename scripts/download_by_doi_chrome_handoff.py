#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

preferred_python = os.getenv("PKB_PYTHON") or os.getenv("PYTHON")
if preferred_python:
    preferred_path = Path(preferred_python).expanduser()
    if preferred_path.exists() and Path(sys.executable).resolve() != preferred_path.resolve():
        os.execv(str(preferred_path), [str(preferred_path), *sys.argv])

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.doi_downloader import (  # noqa: E402
    DEFAULT_PAGE_WAIT_MAX,
    DEFAULT_PAGE_WAIT_MIN,
    DownloadAttempt,
    _classify_access_block_detail,
    _pdf_links_from_page,
    _reason_with_evidence,
    _save_failure_artifacts,
    parse_doi_list,
    publisher_domain,
    run_doi_download_job,
    should_wait_for_manual_access,
)


CHROME_HANDOFF_PROFILE = config.CACHE_DIR / "browser_profiles" / "doi_chrome_handoff"


def _chrome_binary() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("Google Chrome is not installed in /Applications")


def _cdp_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:  # nosec - local CDP probe
            return response.status == 200
    except Exception:
        return False


def _wait_for_cdp(port: int, timeout_seconds: int = 15) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _cdp_ready(port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Chrome CDP port {port} did not become ready")


class ChromeHandoffDownloadSession:
    def __init__(self, port: int, profile_dir: Path, keep_browser_open: bool):
        self.port = port
        self.profile_dir = profile_dir
        self.keep_browser_open = keep_browser_open
        self.process: subprocess.Popen[str] | None = None
        self.playwright = None
        self.browser = None
        self.context = None

    def __enter__(self) -> "ChromeHandoffDownloadSession":
        from playwright.sync_api import sync_playwright  # type: ignore

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        if not _cdp_ready(self.port):
            self.process = subprocess.Popen(
                [
                    _chrome_binary(),
                    f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={self.profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            _wait_for_cdp(self.port)
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        self.context = self.browser.contexts[0]
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        if self.process and not self.keep_browser_open:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _body_text(self, page: Any) -> str:
        try:
            return page.locator("body").inner_text(timeout=5000)
        except Exception:
            return ""

    def _wait_for_manual_clearance(
        self,
        page: Any,
        state: str | None,
        reason: str | None,
        diagnostics: dict[str, Any],
        settings: Any,
    ) -> tuple[str | None, str | None, dict[str, Any]]:
        if state == "blocked_by_access" or not should_wait_for_manual_access(state, settings):
            return state, reason, diagnostics
        print(
            f"需要你在 Chrome handoff 窗口完成登录/验证：{_reason_with_evidence(reason, diagnostics) or state}",
            flush=True,
        )
        deadline = time.monotonic() + settings.manual_login_timeout_seconds
        waited_seconds = 0.0
        while time.monotonic() < deadline:
            time.sleep(2)
            waited_seconds += 2
            state, reason, diagnostics = _classify_access_block_detail(None, page.url, self._body_text(page))
            diagnostics = {
                **diagnostics,
                "manual_access_waited": True,
                "manual_access_wait_seconds": round(waited_seconds, 1),
            }
            if state is None or state == "blocked_by_access":
                break
        return state, reason, diagnostics

    def download(self, doi: str, settings: Any, metadata: dict[str, Any], artifacts_dir: Path) -> DownloadAttempt:
        assert self.context is not None
        page = self.context.new_page()
        landing_url = None
        try:
            target_url = f"https://doi.org/{doi}"
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(max(DEFAULT_PAGE_WAIT_MIN, min(DEFAULT_PAGE_WAIT_MAX, 1.0)))
            landing_url = page.url
            domain = publisher_domain(landing_url)
            state, reason, diagnostics = _classify_access_block_detail(None, landing_url, self._body_text(page))
            state, reason, diagnostics = self._wait_for_manual_clearance(page, state, reason, diagnostics, settings)
            if state:
                screenshot, html = _save_failure_artifacts(page, artifacts_dir, doi)
                return DownloadAttempt(
                    state,
                    page.url,
                    publisher_domain(page.url),
                    None,
                    None,
                    _reason_with_evidence(reason, diagnostics),
                    screenshot,
                    html,
                    diagnostics,
                )

            links = _pdf_links_from_page(page)
            if not links:
                screenshot, html = _save_failure_artifacts(page, artifacts_dir, doi)
                return DownloadAttempt("failed", page.url, domain, None, None, "No reliable PDF link found", screenshot, html)

            pdf_url = links[0]["href"]
            pdf_body = None
            request_status = None
            request_text = ""
            request_content_type = ""
            try:
                pdf_response = self.context.request.get(pdf_url, timeout=60000)
                request_status = pdf_response.status
                request_content_type = (pdf_response.headers.get("content-type", "") or "").lower()
                request_body = pdf_response.body()
                request_text = request_body[:3000].decode("utf-8", errors="ignore")
                if "application/pdf" in request_content_type or request_body.startswith(b"%PDF"):
                    pdf_body = request_body
            except Exception:
                pdf_body = None

            if pdf_body is None:
                # Some publishers reject API-style fetches but allow the real browser page after login.
                nav_response = page.goto(pdf_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(1)
                if nav_response:
                    browser_body = nav_response.body()
                    browser_content_type = (nav_response.headers.get("content-type", "") or "").lower()
                    if "application/pdf" in browser_content_type or browser_body.startswith(b"%PDF"):
                        pdf_body = browser_body

            if pdf_body is not None:
                return DownloadAttempt("downloaded", page.url, domain, pdf_url, pdf_body)

            state, reason, diagnostics = _classify_access_block_detail(request_status, pdf_url, request_text)
            if not state:
                state, reason, diagnostics = _classify_access_block_detail(None, page.url, self._body_text(page))
            if state:
                screenshot, html = _save_failure_artifacts(page, artifacts_dir, doi)
                return DownloadAttempt(
                    state,
                    page.url,
                    domain,
                    pdf_url,
                    None,
                    _reason_with_evidence(reason, diagnostics),
                    screenshot,
                    html,
                    diagnostics,
                )
            return DownloadAttempt("failed", page.url, domain, pdf_url, None, "PDF link did not return a PDF")
        except Exception as exc:
            screenshot, html = _save_failure_artifacts(page, artifacts_dir, doi)
            return DownloadAttempt(
                "failed",
                landing_url or page.url,
                publisher_domain(landing_url or page.url),
                None,
                None,
                str(exc),
                screenshot,
                html,
            )
        finally:
            try:
                page.close()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download DOI PDFs with a real Chrome handoff for manual login/verification."
    )
    parser.add_argument("--doi", action="append", help="Single DOI. Can be repeated.")
    parser.add_argument("--doi-file", help="Text file containing DOI values.")
    parser.add_argument("--out", help="Output directory. Defaults to data/raw/papers.")
    parser.add_argument("--max-items", type=int, default=20, help="Maximum DOI values per batch.")
    parser.add_argument("--manual-login-timeout-seconds", type=int, default=900)
    parser.add_argument("--chrome-debug-port", type=int, default=9223)
    parser.add_argument("--chrome-profile", default=str(CHROME_HANDOFF_PROFILE))
    parser.add_argument("--keep-browser-open", action="store_true")
    parser.add_argument("--auto-ingest", action="store_true")
    parser.add_argument("--rebuild-after-ingest", action="store_true")
    args = parser.parse_args()

    parts = []
    if args.doi:
        parts.extend(args.doi)
    if args.doi_file:
        parts.append(Path(args.doi_file).expanduser().read_text(encoding="utf-8"))
    if not parts or not parse_doi_list("\n".join(parts)):
        parser.error("Provide --doi or --doi-file")

    with ChromeHandoffDownloadSession(
        args.chrome_debug_port,
        Path(args.chrome_profile).expanduser(),
        args.keep_browser_open,
    ) as session:
        result = run_doi_download_job(
            "\n".join(parts),
            {
                "out_dir": args.out,
                "max_items": args.max_items,
                "headed": True,
                "allow_manual_login": True,
                "manual_login_timeout_seconds": args.manual_login_timeout_seconds,
                "auto_ingest": args.auto_ingest,
                "rebuild_after_ingest": args.rebuild_after_ingest,
            },
            browser_runner=session.download,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ready", "partial", "stopped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
