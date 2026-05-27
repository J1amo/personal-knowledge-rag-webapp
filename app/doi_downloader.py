from __future__ import annotations

import json
import random
import shutil
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Callable

from . import config
from .db import connect, init_db, json_dumps, json_loads, utc_now
from .ingest import ingest_file
from .text_utils import file_hash, safe_filename, sha256_bytes

DOI_PATTERN = r"10\.\d{4,9}/[-._;()/:A-Z0-9]+"
DEFAULT_PAGE_WAIT_MIN = 0.3
DEFAULT_PAGE_WAIT_MAX = 1.2
DEFAULT_ARTICLE_DELAY_MIN = 15.0
DEFAULT_ARTICLE_DELAY_MAX = 25.0
FAST_ARTICLE_DELAY_MIN = 5.0
FAST_ARTICLE_DELAY_MAX = 10.0
DEFAULT_MAX_ITEMS = 10
ABSOLUTE_MAX_ITEMS = 20
FAST_MAX_ITEMS = 5
PROFILE_DIR = config.CACHE_DIR / "browser_profiles" / "doi_downloader"
LOG_DIR = config.OUTPUT_DIR / "doi_download_logs"
SNAPSHOT_DIR = LOG_DIR / "snapshots"

STOP_BATCH_STATUSES = {
    "needs_login",
    "blocked_by_captcha",
    "blocked_by_rate_limit",
}
SUCCESS_STATUSES = {"downloaded", "skipped_existing"}

ACCESS_TERMS = (
    "access denied",
    "not authorized",
    "not authorised",
    "subscription required",
    "institutional access",
    "institutional warning",
    "license required",
    "purchase access",
)
CAPTCHA_TERMS = ("captcha", "recaptcha", "hcaptcha", "verify you are human")
RATE_LIMIT_TERMS = ("too many requests", "rate limit", "suspicious activity", "unusual traffic")
LOGIN_TERMS = (
    "log in",
    "login",
    "sign in",
    "single sign-on",
    "shibboleth",
    "ezproxy",
    "mfa",
    "multi-factor",
    "two-factor",
    "institution login",
    "university login",
)


@dataclass
class DoiDownloadSettings:
    out_dir: str
    headed: bool = False
    allow_manual_login: bool = False
    fast_mode: bool = False
    max_items: int = DEFAULT_MAX_ITEMS
    auto_ingest: bool = False
    rebuild_after_ingest: bool = False
    page_action_wait_min: float = DEFAULT_PAGE_WAIT_MIN
    page_action_wait_max: float = DEFAULT_PAGE_WAIT_MAX
    article_delay_min: float = DEFAULT_ARTICLE_DELAY_MIN
    article_delay_max: float = DEFAULT_ARTICLE_DELAY_MAX
    manual_login_timeout_seconds: int = 180
    retry_limit: int = 1


@dataclass
class DownloadAttempt:
    status: str
    landing_url: str | None = None
    publisher_domain: str | None = None
    pdf_url: str | None = None
    pdf_bytes: bytes | None = None
    failure_reason: str | None = None
    screenshot_path: str | None = None
    html_snapshot_path: str | None = None
    diagnostics: dict[str, Any] | None = None


def default_out_dir() -> Path:
    return config.RAW_DIR / "papers"


def normalize_doi(raw: str) -> str:
    value = urllib.parse.unquote((raw or "").strip())
    value = value.strip("<>\"' \t\r\n")
    value = value.replace("\u200b", "")
    for prefix in ("doi:", "DOI:"):
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
    lowered = value.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if lowered.startswith(prefix):
            value = value[len(prefix) :]
            break
    import re

    match = re.search(DOI_PATTERN, value, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid DOI: {raw}")
    return match.group(0).strip().rstrip(".,;").lower()


def parse_doi_list(text: str) -> list[str]:
    import re

    matches = re.findall(DOI_PATTERN, urllib.parse.unquote(text or ""), flags=re.IGNORECASE)
    if not matches and text.strip():
        try:
            return [normalize_doi(text)]
        except ValueError:
            return []
    return dedupe_dois(matches)


def dedupe_dois(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        doi = normalize_doi(value)
        if doi not in seen:
            seen.add(doi)
            result.append(doi)
    return result


def resolve_settings(payload: dict[str, Any] | DoiDownloadSettings | None = None) -> DoiDownloadSettings:
    if isinstance(payload, DoiDownloadSettings):
        settings = payload
    else:
        payload = dict(payload or {})
        settings = DoiDownloadSettings(
            out_dir=str(Path(payload.get("out_dir") or default_out_dir()).expanduser()),
            headed=bool(payload.get("headed")),
            allow_manual_login=bool(payload.get("allow_manual_login")),
            fast_mode=bool(payload.get("fast_mode")),
            max_items=int(payload.get("max_items") or DEFAULT_MAX_ITEMS),
            auto_ingest=bool(payload.get("auto_ingest")),
            rebuild_after_ingest=bool(payload.get("rebuild_after_ingest")),
        )
    if settings.fast_mode:
        settings.max_items = max(1, min(settings.max_items, FAST_MAX_ITEMS))
        settings.article_delay_min = FAST_ARTICLE_DELAY_MIN
        settings.article_delay_max = FAST_ARTICLE_DELAY_MAX
    else:
        settings.max_items = max(1, min(settings.max_items, ABSOLUTE_MAX_ITEMS))
        settings.article_delay_min = DEFAULT_ARTICLE_DELAY_MIN
        settings.article_delay_max = DEFAULT_ARTICLE_DELAY_MAX
    if settings.page_action_wait_min < 0:
        settings.page_action_wait_min = DEFAULT_PAGE_WAIT_MIN
    if settings.page_action_wait_max < settings.page_action_wait_min:
        settings.page_action_wait_max = max(settings.page_action_wait_min, DEFAULT_PAGE_WAIT_MAX)
    return settings


def doi_downloader_status() -> dict[str, Any]:
    profile = PROFILE_DIR
    return {
        "playwright_installed": find_spec("playwright") is not None,
        "profile_dir": str(profile),
        "profile_exists": profile.exists(),
        "default_out_dir": str(default_out_dir()),
        "log_dir": str(LOG_DIR),
        "default_policy": {
            "concurrency": 1,
            "max_items_per_run": DEFAULT_MAX_ITEMS,
            "absolute_max_items_per_run": ABSOLUTE_MAX_ITEMS,
            "page_action_wait_seconds": [DEFAULT_PAGE_WAIT_MIN, DEFAULT_PAGE_WAIT_MAX],
            "article_delay_seconds": [DEFAULT_ARTICLE_DELAY_MIN, DEFAULT_ARTICLE_DELAY_MAX],
            "fast_mode_default": False,
            "fast_mode_article_delay_seconds": [FAST_ARTICLE_DELAY_MIN, FAST_ARTICLE_DELAY_MAX],
            "fast_mode_max_items": FAST_MAX_ITEMS,
        },
        "setup_hint": (
            "Install with: python -m pip install playwright && python -m playwright install chromium"
        ),
    }


def clear_browser_profile() -> dict[str, Any]:
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
        return {"status": "ready", "message": "DOI downloader browser profile cleared", "profile_dir": str(PROFILE_DIR)}
    return {"status": "noop", "message": "DOI downloader browser profile did not exist", "profile_dir": str(PROFILE_DIR)}


def _classify_access_block_detail(
    status_code: int | None, url: str | None, text: str | None
) -> tuple[str | None, str | None, dict[str, Any]]:
    haystack = " ".join([url or "", text or ""]).lower()
    checks = [
        (
            "blocked_by_rate_limit",
            "Rate limit, 429, or suspicious activity warning detected",
            RATE_LIMIT_TERMS,
            status_code == 429,
        ),
        (
            "blocked_by_access",
            "Access denied or institutional access warning detected",
            ACCESS_TERMS,
            status_code == 403,
        ),
        ("blocked_by_captcha", "CAPTCHA or human verification detected", CAPTCHA_TERMS, False),
        ("needs_login", "Login, MFA, Shibboleth, or EZproxy page detected", LOGIN_TERMS, False),
    ]
    for state, reason, terms, status_match in checks:
        matched_terms = [term for term in terms if term in haystack]
        if status_match or matched_terms:
            diagnostics = {
                "http_status": status_code,
                "url": url,
                "matched_terms": matched_terms[:8],
                "matched_by_http_status": bool(status_match),
                "classification": state,
            }
            return state, reason, diagnostics
    return None, None, {"http_status": status_code, "url": url, "matched_terms": []}


def _reason_with_evidence(reason: str | None, diagnostics: dict[str, Any] | None) -> str | None:
    if not reason:
        return None
    diagnostics = diagnostics or {}
    signals = list(diagnostics.get("matched_terms") or [])
    if diagnostics.get("matched_by_http_status") and diagnostics.get("http_status"):
        signals.insert(0, f"HTTP {diagnostics['http_status']}")
    if not signals:
        return reason
    return f"{reason} (signals: {', '.join(signals[:6])})"


def classify_access_block(status_code: int | None, url: str | None, text: str | None) -> tuple[str | None, str | None]:
    state, reason, _diagnostics = _classify_access_block_detail(status_code, url, text)
    return state, reason


def publisher_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def fetch_crossref_metadata(doi: str, timeout: int = 8) -> dict[str, Any]:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    request = urllib.request.Request(url, headers={"User-Agent": "personal-research-os-doi-downloader/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - public metadata endpoint
            payload = json.loads(response.read().decode("utf-8"))
        message = payload.get("message", {})
        authors = []
        for author in message.get("author", [])[:12]:
            name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
            if name:
                authors.append(name)
        year_parts = (
            message.get("published-print", {}).get("date-parts")
            or message.get("published-online", {}).get("date-parts")
            or message.get("created", {}).get("date-parts")
            or []
        )
        year = year_parts[0][0] if year_parts and year_parts[0] else None
        return {
            "doi": doi,
            "title": (message.get("title") or [""])[0],
            "authors": authors,
            "journal": (message.get("container-title") or [""])[0],
            "year": year,
            "publisher": message.get("publisher"),
            "metadata_source": "crossref",
            "metadata_status": "ready",
        }
    except Exception as exc:
        return {"doi": doi, "metadata_source": "crossref", "metadata_status": "failed", "metadata_error": str(exc)}


def _first_author_or_publisher(metadata: dict[str, Any]) -> str:
    authors = metadata.get("authors") or []
    if authors:
        return str(authors[0]).split()[-1]
    return str(metadata.get("publisher") or "publisher")


def build_pdf_basename(doi: str, metadata: dict[str, Any]) -> str:
    year = str(metadata.get("year") or "unknown")
    creator = _first_author_or_publisher(metadata)
    title = str(metadata.get("title") or doi.split("/", 1)[-1])
    suffix = doi.split("/", 1)[-1]
    return safe_filename(f"{year}_{creator}_{title[:80]}_{suffix}")[:180]


def save_pdf_and_metadata(
    *,
    doi: str,
    pdf_bytes: bytes,
    out_dir: Path,
    metadata: dict[str, Any],
    landing_url: str | None,
    pdf_url: str | None,
    domain: str | None,
) -> dict[str, Any]:
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("Downloaded content is not a PDF")
    out_dir.mkdir(parents=True, exist_ok=True)
    basename = build_pdf_basename(doi, metadata)
    pdf_path = out_dir / f"{basename}.pdf"
    counter = 2
    while pdf_path.exists():
        pdf_path = out_dir / f"{basename}_{counter}.pdf"
        counter += 1
    pdf_path.write_bytes(pdf_bytes)
    digest = file_hash(pdf_path)
    sidecar = pdf_path.with_suffix(".metadata.json")
    payload = {
        **metadata,
        "doi": doi,
        "landing_page_url": landing_url,
        "final_pdf_url": pdf_url,
        "publisher_domain": domain,
        "downloaded_at": utc_now(),
        "file_path": str(pdf_path),
        "file_hash": digest,
        "status": "downloaded",
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved_path": str(pdf_path), "metadata_path": str(sidecar), "file_hash": digest, "metadata": payload}


def find_existing_download(doi: str, out_dir: Path) -> dict[str, Any] | None:
    if not out_dir.exists():
        return None
    for sidecar in out_dir.glob("*.metadata.json"):
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            sidecar_doi = normalize_doi(payload.get("doi") or "")
        except (OSError, json.JSONDecodeError):
            continue
        except ValueError:
            continue
        if sidecar_doi == doi:
            path = Path(payload.get("file_path") or sidecar.with_suffix(".pdf"))
            if path.exists():
                return {"saved_path": str(path), "metadata_path": str(sidecar), "file_hash": payload.get("file_hash")}
    return None


def _db_existing_download(doi: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            """
            SELECT saved_path, metadata_path, file_hash
            FROM doi_download_items
            WHERE doi=? AND status IN ('downloaded', 'skipped_existing')
              AND saved_path IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (doi,),
        ).fetchone()
    if not row:
        return None
    saved_path = Path(row["saved_path"])
    if saved_path.exists():
        return dict(row)
    return None


def _create_job(dois: list[str], settings: DoiDownloadSettings, input_count: int) -> str:
    job_id = "doi_job_" + uuid.uuid4().hex
    now = utc_now()
    with connect() as con:
        con.execute(
            """
            INSERT INTO doi_download_jobs (
              job_id, status, input_count, requested_count, settings_json,
              summary_json, failure_reason, created_at, updated_at
            )
            VALUES (?, 'running', ?, ?, ?, '{}', NULL, ?, ?)
            """,
            (job_id, input_count, len(dois), json_dumps(asdict(settings)), now, now),
        )
    return job_id


def _create_item(job_id: str, doi: str) -> str:
    item_id = "doi_item_" + uuid.uuid4().hex
    now = utc_now()
    with connect() as con:
        con.execute(
            """
            INSERT INTO doi_download_items (id, job_id, doi, status, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (item_id, job_id, doi, now, now),
        )
    return item_id


def _update_item(item_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "landing_url",
        "publisher_domain",
        "pdf_url",
        "saved_path",
        "metadata_path",
        "file_hash",
        "failure_reason",
        "screenshot_path",
        "html_snapshot_path",
        "ingestion_source_id",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key}=?" for key in updates)
    with connect() as con:
        con.execute(f"UPDATE doi_download_items SET {assignments} WHERE id=?", [*updates.values(), item_id])


def _finish_job(job_id: str, status: str, summary: dict[str, Any], failure_reason: str | None = None) -> None:
    with connect() as con:
        con.execute(
            """
            UPDATE doi_download_jobs
            SET status=?, summary_json=?, failure_reason=?, updated_at=?
            WHERE job_id=?
            """,
            (status, json_dumps(summary), failure_reason, utc_now(), job_id),
        )


def _record_metadata(doi: str, metadata: dict[str, Any]) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO doi_metadata (doi, metadata_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(doi) DO UPDATE SET
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (doi, json_dumps(metadata), utc_now()),
        )


def list_doi_download_jobs(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT job_id, status, input_count, requested_count, settings_json,
                   summary_json, failure_reason, created_at, updated_at
            FROM doi_download_jobs
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["settings"] = json_loads(item.pop("settings_json"), {})
        item["summary"] = json_loads(item.pop("summary_json"), {})
        result.append(item)
    return result


def list_doi_download_items(job_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    query = """
        SELECT id, job_id, doi, status, landing_url, publisher_domain, pdf_url,
               saved_path, metadata_path, file_hash, failure_reason,
               screenshot_path, html_snapshot_path, ingestion_source_id,
               created_at, updated_at
        FROM doi_download_items
    """
    params: list[Any] = []
    if job_id:
        query += " WHERE job_id=?"
        params.append(job_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with connect() as con:
        rows = con.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _write_job_log(job_id: str, summary: dict[str, Any], items: list[dict[str, Any]]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{job_id}.json"
    log_summary = {**summary, "log_path": str(path)}
    path.write_text(
        json.dumps({"job_id": job_id, "written_at": utc_now(), "summary": log_summary, "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _save_failure_artifacts(page: Any, artifacts_dir: Path, doi: str) -> tuple[str | None, str | None]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    base = safe_filename(doi)
    screenshot_path = artifacts_dir / f"{base}_{uuid.uuid4().hex[:8]}.png"
    html_path = artifacts_dir / f"{base}_{uuid.uuid4().hex[:8]}.html"
    screenshot = None
    html = None
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        screenshot = str(screenshot_path)
    except Exception:
        pass
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        html = str(html_path)
    except Exception:
        pass
    return screenshot, html


def _pdf_links_from_page(page: Any) -> list[dict[str, str]]:
    return page.evaluate(
        """
        () => {
          const links = [];
          document.querySelectorAll('meta').forEach((meta) => {
            const name = (meta.getAttribute('name') || meta.getAttribute('property') || '').toLowerCase();
            const content = meta.getAttribute('content') || '';
            if (content && name.includes('citation_pdf_url')) {
              links.push({ href: content, text: 'citation_pdf_url', source: 'meta' });
            }
          });
          document.querySelectorAll('a[href]').forEach((a) => {
            const href = a.href;
            const text = (a.textContent || a.getAttribute('aria-label') || '').trim();
            const lowerHref = href.toLowerCase();
            const lowerText = text.toLowerCase();
            if (lowerHref.endsWith('.pdf') || lowerHref.includes('/pdf') ||
                lowerText === 'pdf' || lowerText.includes('download pdf') ||
                lowerText.includes('article pdf') || lowerText.includes('view pdf')) {
              links.push({ href, text, source: 'link' });
            }
          });
          return links;
        }
        """
    )


class PlaywrightDownloadSession:
    def __init__(self, settings: DoiDownloadSettings):
        self.settings = settings
        self.playwright = None
        self.context = None

    def __enter__(self) -> "PlaywrightDownloadSession":
        from playwright.sync_api import sync_playwright  # type: ignore

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=not self.settings.headed,
            accept_downloads=True,
            viewport={"width": 1280, "height": 900},
        )
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()

    def _jitter(self) -> None:
        time.sleep(random.uniform(self.settings.page_action_wait_min, self.settings.page_action_wait_max))

    def _body_text(self, page: Any) -> str:
        try:
            return page.locator("body").inner_text(timeout=5000)
        except Exception:
            return ""

    def download(self, doi: str, metadata: dict[str, Any], artifacts_dir: Path) -> DownloadAttempt:
        assert self.context is not None
        page = self.context.new_page()
        landing_url = None
        try:
            target_url = f"https://doi.org/{doi}"
            response = page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            self._jitter()
            landing_url = page.url
            domain = publisher_domain(landing_url)
            status_code = response.status if response else None
            content_type = (response.headers.get("content-type", "") if response else "").lower()
            if response and ("application/pdf" in content_type or landing_url.lower().endswith(".pdf")):
                body = response.body()
                return DownloadAttempt("downloaded", landing_url, domain, landing_url, body)
            state, reason, diagnostics = _classify_access_block_detail(status_code, landing_url, self._body_text(page))
            if state == "needs_login" and self.settings.allow_manual_login and self.settings.headed:
                deadline = time.monotonic() + self.settings.manual_login_timeout_seconds
                while time.monotonic() < deadline:
                    time.sleep(2)
                    state, reason, diagnostics = _classify_access_block_detail(None, page.url, self._body_text(page))
                    if state != "needs_login":
                        break
            if state and state != "blocked_by_access":
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
                if state == "blocked_by_access":
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
                return DownloadAttempt("failed", page.url, domain, None, None, "No reliable PDF link found", screenshot, html)

            pdf_url = links[0]["href"]
            pdf_response = self.context.request.get(pdf_url, timeout=60000)
            self._jitter()
            pdf_status = pdf_response.status
            pdf_body = pdf_response.body()
            pdf_text = pdf_body[:3000].decode("utf-8", errors="ignore")
            state, reason, diagnostics = _classify_access_block_detail(pdf_status, pdf_url, pdf_text)
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
            content_type = (pdf_response.headers.get("content-type", "") or "").lower()
            if "application/pdf" not in content_type and not pdf_body.startswith(b"%PDF"):
                return DownloadAttempt("failed", page.url, domain, pdf_url, None, "PDF link did not return a PDF")
            return DownloadAttempt("downloaded", page.url, domain, pdf_url, pdf_body)
        except Exception as exc:
            screenshot, html = _save_failure_artifacts(page, artifacts_dir, doi)
            return DownloadAttempt("failed", landing_url or page.url, publisher_domain(landing_url or page.url), None, None, str(exc), screenshot, html)
        finally:
            try:
                page.close()
            except Exception:
                pass


def _ingest_downloaded_pdf(path: str, doi: str, metadata: dict[str, Any], rebuild_after: bool) -> dict[str, Any]:
    result = ingest_file(Path(path), domain="paper", topic="doi_download", sensitivity="public")
    payload = result.to_dict()
    if result.source_id:
        with connect() as con:
            con.execute(
                "UPDATE sources SET notes=? WHERE source_id=?",
                (json_dumps({"doi": doi, "doi_metadata": metadata}), result.source_id),
            )
    if rebuild_after and result.status in {"ready", "duplicate"}:
        from .indexes import rebuild_indexes

        payload["rebuild"] = rebuild_indexes(source_id=result.source_id)
    return payload


def _sleep_article(settings: DoiDownloadSettings, sleeper: Callable[[float], None] | None) -> float:
    seconds = random.uniform(settings.article_delay_min, settings.article_delay_max)
    if sleeper:
        sleeper(seconds)
    else:
        time.sleep(seconds)
    return seconds


def run_doi_download_job(
    doi_text: str,
    settings: dict[str, Any] | DoiDownloadSettings | None = None,
    *,
    browser_runner: Callable[[str, DoiDownloadSettings, dict[str, Any], Path], DownloadAttempt] | None = None,
    metadata_fetcher: Callable[[str], dict[str, Any]] = fetch_crossref_metadata,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    init_db()
    resolved = resolve_settings(settings)
    all_dois = dedupe_dois(parse_doi_list(doi_text))
    selected = all_dois[: resolved.max_items]
    job_id = _create_job(selected, resolved, len(all_dois))
    out_dir = Path(resolved.out_dir).expanduser()
    artifacts_dir = SNAPSHOT_DIR / job_id
    items: list[dict[str, Any]] = []
    stop_reason = None

    if not selected:
        summary = {
            "status_counts": {"failed": 0},
            "message": "No valid DOI supplied",
            "input_count": len(all_dois),
            "requested_count": 0,
            "processed_count": 0,
            "unprocessed_count": 0,
            "stopped_reason": "No valid DOI supplied",
            "log_dir": str(LOG_DIR),
            "profile_dir": str(PROFILE_DIR),
            "settings": asdict(resolved),
        }
        log_path = _write_job_log(job_id, summary, [])
        summary["log_path"] = str(log_path)
        _finish_job(job_id, "failed", summary, "No valid DOI supplied")
        return {"status": "failed", "job_id": job_id, "summary": summary, "items": []}

    runner_context = nullcontext(browser_runner)
    if browser_runner is None and find_spec("playwright") is not None:
        runner_context = PlaywrightDownloadSession(resolved)

    with runner_context as runner:
        for idx, doi in enumerate(selected, start=1):
            item_id = _create_item(job_id, doi)
            metadata = metadata_fetcher(doi)
            metadata = {"doi": doi, **metadata}
            _record_metadata(doi, metadata)

            existing = find_existing_download(doi, out_dir) or _db_existing_download(doi)
            if existing:
                _update_item(
                    item_id,
                    status="skipped_existing",
                    saved_path=existing.get("saved_path"),
                    metadata_path=existing.get("metadata_path"),
                    file_hash=existing.get("file_hash"),
                )
                items.append({"id": item_id, "doi": doi, "status": "skipped_existing", **existing})
                continue

            if browser_runner is None and find_spec("playwright") is None:
                reason = "Playwright is not installed; run python -m pip install playwright and python -m playwright install chromium"
                _update_item(item_id, status="failed", failure_reason=reason)
                items.append({"id": item_id, "doi": doi, "status": "failed", "failure_reason": reason})
                continue

            _update_item(item_id, status="resolving")
            attempt = runner.download(doi, metadata, artifacts_dir) if hasattr(runner, "download") else runner(doi, resolved, metadata, artifacts_dir)
            if attempt.status == "downloaded" and attempt.pdf_bytes:
                try:
                    saved = save_pdf_and_metadata(
                        doi=doi,
                        pdf_bytes=attempt.pdf_bytes,
                        out_dir=out_dir,
                        metadata=metadata,
                        landing_url=attempt.landing_url,
                        pdf_url=attempt.pdf_url,
                        domain=attempt.publisher_domain,
                    )
                    ingestion_source_id = None
                    if resolved.auto_ingest:
                        ingest_result = _ingest_downloaded_pdf(
                            saved["saved_path"], doi, saved["metadata"], resolved.rebuild_after_ingest
                        )
                        ingestion_source_id = ingest_result.get("source_id")
                    _update_item(
                        item_id,
                        status="downloaded",
                        landing_url=attempt.landing_url,
                        publisher_domain=attempt.publisher_domain,
                        pdf_url=attempt.pdf_url,
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
                            "landing_url": attempt.landing_url,
                            "publisher_domain": attempt.publisher_domain,
                            "pdf_url": attempt.pdf_url,
                            "saved_path": saved["saved_path"],
                            "metadata_path": saved["metadata_path"],
                            "file_hash": saved["file_hash"],
                            "ingestion_source_id": ingestion_source_id,
                        }
                    )
                except Exception as exc:
                    _update_item(item_id, status="failed", failure_reason=str(exc))
                    items.append({"id": item_id, "doi": doi, "status": "failed", "failure_reason": str(exc)})
            else:
                _update_item(
                    item_id,
                    status=attempt.status,
                    landing_url=attempt.landing_url,
                    publisher_domain=attempt.publisher_domain,
                    pdf_url=attempt.pdf_url,
                    failure_reason=attempt.failure_reason,
                    screenshot_path=attempt.screenshot_path,
                    html_snapshot_path=attempt.html_snapshot_path,
                )
                items.append(
                    {
                        "id": item_id,
                        "doi": doi,
                        "status": attempt.status,
                        "landing_url": attempt.landing_url,
                        "publisher_domain": attempt.publisher_domain,
                        "pdf_url": attempt.pdf_url,
                        "failure_reason": attempt.failure_reason,
                        "screenshot_path": attempt.screenshot_path,
                        "html_snapshot_path": attempt.html_snapshot_path,
                        "diagnostics": attempt.diagnostics,
                    }
                )
                if attempt.status in STOP_BATCH_STATUSES:
                    stop_reason = attempt.failure_reason or attempt.status
                    break

            if idx < len(selected) and not stop_reason:
                _sleep_article(resolved, sleeper)

    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    if stop_reason:
        job_status = "stopped"
    elif counts and all(status in SUCCESS_STATUSES for status in counts):
        job_status = "ready"
    elif counts.get("downloaded") or counts.get("skipped_existing"):
        job_status = "partial"
    else:
        job_status = "failed"
    summary = {
        "status_counts": counts,
        "input_count": len(all_dois),
        "requested_count": len(selected),
        "processed_count": len(items),
        "unprocessed_count": max(0, len(selected) - len(items)),
        "stopped_reason": stop_reason,
        "stop_batch_statuses": sorted(STOP_BATCH_STATUSES),
        "continue_item_statuses": ["blocked_by_access"],
        "log_dir": str(LOG_DIR),
        "profile_dir": str(PROFILE_DIR),
        "settings": asdict(resolved),
    }
    log_path = _write_job_log(job_id, summary, items)
    summary["log_path"] = str(log_path)
    _finish_job(job_id, job_status, summary, stop_reason)
    return {"status": job_status, "job_id": job_id, "summary": summary, "items": items}
