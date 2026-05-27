#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
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
    apply_candidate_manual_wait_policy,
    doi_landing_candidates,
    no_authorized_landing_attempt,
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
    def __init__(
        self,
        port: int,
        profile_dir: Path,
        keep_browser_open: bool,
        *,
        background: bool,
        focus_on_manual: bool,
        use_deepseek: bool,
    ):
        self.port = port
        self.profile_dir = profile_dir
        self.keep_browser_open = keep_browser_open
        self.background = background
        self.focus_on_manual = focus_on_manual
        self.use_deepseek = use_deepseek
        self.process: subprocess.Popen[str] | None = None
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self) -> "ChromeHandoffDownloadSession":
        from playwright.sync_api import sync_playwright  # type: ignore

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        if not _cdp_ready(self.port):
            args = [
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-minimized",
                "about:blank",
            ]
            if self.background and sys.platform == "darwin":
                self.process = subprocess.Popen(
                    ["open", "-g", "-n", "-a", "Google Chrome", "--args", *args],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            else:
                self.process = subprocess.Popen(
                    [_chrome_binary(), *args],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            _wait_for_cdp(self.port)
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        self.context = self.browser.contexts[0]
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
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
        candidate: dict[str, Any],
    ) -> tuple[str | None, str | None, dict[str, Any]]:
        state, reason, diagnostics = apply_candidate_manual_wait_policy(state, reason, diagnostics, candidate)
        if state == "blocked_by_access" or not should_wait_for_manual_access(state, settings):
            return state, reason, diagnostics
        if self.focus_on_manual and sys.platform == "darwin":
            subprocess.run(["open", "-a", "Google Chrome"], check=False)
        print(
            "需要你在 Chrome handoff 窗口完成登录/验证。"
            f"页面已停在后台专用 Chrome profile：{page.url}\n"
            f"原因：{_reason_with_evidence(reason, diagnostics) or state}",
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
            state, reason, diagnostics = apply_candidate_manual_wait_policy(state, reason, diagnostics, candidate)
            if state is None or state == "blocked_by_access":
                break
        return state, reason, diagnostics

    def _pdf_bytes_from_page_fetch(self, page: Any, pdf_url: str) -> tuple[int | None, str, bytes | None]:
        try:
            result = page.evaluate(
                """
                async (url) => {
                  const response = await fetch(url, { credentials: 'include' });
                  const buffer = await response.arrayBuffer();
                  const bytes = new Uint8Array(buffer);
                  let binary = '';
                  const chunkSize = 0x8000;
                  for (let i = 0; i < bytes.length; i += chunkSize) {
                    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
                  }
                  return {
                    status: response.status,
                    contentType: response.headers.get('content-type') || '',
                    bodyBase64: btoa(binary),
                  };
                }
                """,
                pdf_url,
            )
            body = _extract_pdf_bytes(base64.b64decode(result.get("bodyBase64") or ""))
            return int(result.get("status") or 0), str(result.get("contentType") or ""), body
        except Exception:
            return None, "", None

    def _download_from_target(
        self,
        doi: str,
        settings: Any,
        metadata: dict[str, Any],
        artifacts_dir: Path,
        candidate: dict[str, Any],
    ) -> DownloadAttempt:
        assert self.context is not None
        page = self.page or self.context.new_page()
        landing_url = None
        try:
            target_url = str(candidate.get("href") or f"https://doi.org/{doi}")
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(max(DEFAULT_PAGE_WAIT_MIN, min(DEFAULT_PAGE_WAIT_MAX, 1.0)))
            landing_url = page.url
            domain = publisher_domain(landing_url)
            state, reason, diagnostics = _classify_access_block_detail(None, landing_url, self._body_text(page))
            links = _pdf_links_from_page(page)
            diagnostics = {**diagnostics, "landing_candidate": candidate}
            defer_manual_wait = bool(state in {"needs_login", "blocked_by_access"} and links)
            if defer_manual_wait:
                diagnostics = {
                    **diagnostics,
                    "manual_access_wait_deferred_for_pdf_links": True,
                    "pdf_link_count_before_manual_wait": len(links),
                }
            else:
                state, reason, diagnostics = self._wait_for_manual_clearance(
                    page, state, reason, diagnostics, settings, candidate
                )
                diagnostics = {**diagnostics, "landing_candidate": candidate}
            if state and not defer_manual_wait:
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

            if self.use_deepseek:
                advice = _deepseek_page_advice(page, links)
                if advice.get("status") == "access_denied":
                    screenshot, html = _save_failure_artifacts(page, artifacts_dir, doi)
                    return DownloadAttempt(
                        "blocked_by_access",
                        page.url,
                        domain,
                        None,
                        None,
                        advice.get("reason") or "DeepSeek page advisor classified page as access denied",
                        screenshot,
                        html,
                        {"deepseek_page_advice": advice},
                    )
                if advice.get("pdf_url"):
                    links = [{"href": advice["pdf_url"], "text": "deepseek_page_advice", "source": "deepseek"}]
            if not links:
                screenshot, html = _save_failure_artifacts(page, artifacts_dir, doi)
                return DownloadAttempt("failed", page.url, domain, None, None, "No reliable PDF link found", screenshot, html)

            pdf_url = links[0]["href"]
            pdf_body = None
            request_status = None
            request_text = ""
            request_content_type = ""
            fetch_status, fetch_content_type, fetch_body = self._pdf_bytes_from_page_fetch(page, pdf_url)
            request_status = fetch_status
            request_content_type = fetch_content_type
            if fetch_body is not None:
                pdf_body = fetch_body
            try:
                if pdf_body is None:
                    pdf_response = self.context.request.get(pdf_url, timeout=60000)
                    request_status = pdf_response.status
                    request_content_type = (pdf_response.headers.get("content-type", "") or "").lower()
                    request_body = pdf_response.body()
                    request_text = request_body[:3000].decode("utf-8", errors="ignore")
                    pdf_body = _extract_pdf_bytes(request_body)
            except Exception:
                pass

            if pdf_body is None:
                # Some publishers reject API-style fetches but allow the real browser page after login.
                nav_response = page.goto(pdf_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(1)
                if nav_response:
                    browser_body = nav_response.body()
                    browser_content_type = (nav_response.headers.get("content-type", "") or "").lower()
                    pdf_body = _extract_pdf_bytes(browser_body)

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
            pass

    def download(self, doi: str, settings: Any, metadata: dict[str, Any], artifacts_dir: Path) -> DownloadAttempt:
        last_attempt: DownloadAttempt | None = None
        candidates = doi_landing_candidates(doi, metadata)
        if not candidates:
            return no_authorized_landing_attempt(doi, metadata)
        for candidate in candidates:
            attempt = self._download_from_target(doi, settings, metadata, artifacts_dir, candidate)
            if attempt.status == "downloaded":
                return attempt
            last_attempt = attempt
            if attempt.status in {"needs_login", "blocked_by_captcha", "blocked_by_rate_limit"}:
                return attempt
        if last_attempt:
            return last_attempt
        return DownloadAttempt("failed", None, None, None, None, "No DOI landing candidate found")


def _extract_pdf_bytes(body: bytes) -> bytes | None:
    offset = body.find(b"%PDF")
    if 0 <= offset <= 1024:
        return body[offset:]
    return None


def _page_link_candidates(page: Any) -> list[dict[str, str]]:
    try:
        return page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href], button, [role="button"]')).slice(0, 80).map((el) => ({
              tag: el.tagName,
              text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 160),
              href: el.href || el.getAttribute('href') || '',
              aria: el.getAttribute('aria-label') || '',
            }))
            """
        )
    except Exception:
        return []


def _deepseek_page_advice(page: Any, links: list[dict[str, str]]) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return {}
    base_url = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash"
    try:
        page_state = page.evaluate(
            """
            () => ({
              url: location.href,
              title: document.title,
              text: (document.body?.innerText || '').slice(0, 5000),
            })
            """
        )
    except Exception:
        return {}
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You classify publisher article pages for an authorized DOI downloader. "
                    "Never suggest bypassing CAPTCHA, paywalls, login, or access controls. "
                    "Return only JSON with keys status, pdf_url, reason. "
                    "status must be one of access_denied, login_required, captcha_or_security, "
                    "pdf_available, no_pdf_unknown. pdf_url must be one of the provided candidate hrefs or empty."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "page": page_state,
                        "existing_pdf_links": links[:20],
                        "link_candidates": _page_link_candidates(page),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "max_tokens": 300,
    }
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec - user-configured DeepSeek endpoint
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        allowed_hrefs = {item.get("href") for item in [*links, *_page_link_candidates(page)] if item.get("href")}
        if parsed.get("pdf_url") and parsed["pdf_url"] not in allowed_hrefs:
            parsed["pdf_url"] = ""
        return parsed
    except Exception as exc:
        return {"status": "no_pdf_unknown", "pdf_url": "", "reason": f"DeepSeek advisor failed: {exc}"}


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
    parser.add_argument("--foreground", action="store_true", help="Allow Chrome to come to the foreground when launched.")
    parser.add_argument("--focus-on-manual", action="store_true", help="Bring Chrome forward only when user action is needed.")
    parser.add_argument("--use-deepseek", action="store_true", help="Use DeepSeek API for page-state/link advice when DEEPSEEK_API_KEY is set.")
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
        background=not args.foreground,
        focus_on_manual=args.focus_on_manual,
        use_deepseek=args.use_deepseek,
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
