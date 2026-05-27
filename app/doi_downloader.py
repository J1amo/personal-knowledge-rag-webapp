from __future__ import annotations

import json
import random
import re
import shutil
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
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
DEFAULT_MANUAL_LOGIN_TIMEOUT_SECONDS = 900
MAX_MANUAL_LOGIN_TIMEOUT_SECONDS = 3600
PROFILE_DIR = config.CACHE_DIR / "browser_profiles" / "doi_downloader"
LOG_DIR = config.OUTPUT_DIR / "doi_download_logs"
SNAPSHOT_DIR = LOG_DIR / "snapshots"
SERIALS_SOLUTIONS_BASE_URL = "https://jn2xs2wb8u.search.serialssolutions.com/"
SERIALS_SOLUTIONS_LIB_HASH = "JN2XS2WB8U"

STOP_BATCH_STATUSES = {
    "needs_login",
    "blocked_by_captcha",
    "blocked_by_rate_limit",
}
MANUAL_ACCESS_WAIT_STATUSES = {"needs_login", "blocked_by_access", "blocked_by_captcha"}
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
    "access is not provided via",
)
CAPTCHA_TERMS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "verify you are human",
    "are you a robot",
    "cloudflare",
    "ray id",
    "security verification",
    "security check",
    "making sure you're not a bot",
    "making sure you&#39;re not a bot",
    "not a bot",
    "bot check",
    "anubis",
    "proof-of-work",
    "checking your browser",
    "checking if the site connection is secure",
    "validate.perfdrive.com",
    "botmanager_support",
    "正在进行安全验证",
    "正在验证",
    "安全服务防护恶意自动程序",
    "验证您不是自动程序",
)
LINK_RESOLVER_FULLTEXT_TERMS = ("フルテキスト", "full text")
LINK_RESOLVER_OPEN_ACCESS_TERMS = ("オープンアクセス", "open access")
LINK_RESOLVER_SKIP_LABEL_TERMS = (
    "refworks",
    "ulrichs",
    "書誌情報",
    "ジャーナルを見る",
    "journal",
    "opac",
    "文献複写",
    "e-dds",
)
LINK_RESOLVER_PUBLIC_HOSTS = (
    "hal.science",
    "laas.hal.science",
    "ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "zenodo.org",
)
SCHOOL_AUTHORIZED_PLATFORM_RULES: tuple[dict[str, Any], ...] = (
    {
        "name": "ACM Digital Library Open Access",
        "hosts": ("dl.acm.org",),
        "metadata_terms": ("association for computing machinery", "acm"),
        "open_access_only": True,
    },
    {
        "name": "AIP Journals Complete",
        "hosts": ("pubs.aip.org", "aip.scitation.org"),
        "metadata_terms": ("aip publishing", "american institute of physics"),
        "campus_only": True,
    },
    {
        "name": "American Chemical Society Journals",
        "hosts": ("pubs.acs.org",),
        "metadata_terms": ("american chemical society", "acs"),
    },
    {
        "name": "AMA Current Titles",
        "hosts": ("jamanetwork.com", "ama-assn.org"),
        "metadata_terms": ("american medical association", "jamanetwork", "ama"),
    },
    {
        "name": "American Association for Cancer Research",
        "hosts": ("aacrjournals.org",),
        "metadata_terms": ("american association for cancer research", "aacr"),
    },
    {
        "name": "American Mathematical Society Publications",
        "hosts": ("ams.org", "www.ams.org"),
        "metadata_terms": ("american mathematical society", "ams"),
        "campus_only": True,
    },
    {
        "name": "American Physical Society Journals",
        "hosts": ("journals.aps.org", "link.aps.org"),
        "metadata_terms": ("american physical society",),
    },
    {
        "name": "Business Source Premier",
        "hosts": ("ebscohost.com", "search.ebscohost.com"),
        "metadata_terms": ("business source premier", "ebsco"),
    },
    {
        "name": "Cambridge Core All Books",
        "hosts": ("cambridge.org",),
        "metadata_terms": ("cambridge university press", "cambridge core"),
    },
    {
        "name": "Cambridge Journals: 2026 Full Collection",
        "hosts": ("cambridge.org",),
        "metadata_terms": ("cambridge university press",),
    },
    {
        "name": "DOAB: Directory of Open Access Books",
        "hosts": ("doabooks.org",),
        "metadata_terms": ("directory of open access books", "doab"),
        "open_access": True,
    },
    {
        "name": "DOAJ Directory of Open Access Journals",
        "hosts": ("doaj.org",),
        "metadata_terms": ("directory of open access journals", "doaj"),
        "open_access": True,
    },
    {
        "name": "Elsevier ScienceDirect Journals",
        "hosts": ("sciencedirect.com", "www.sciencedirect.com"),
        "metadata_terms": ("elsevier",),
    },
    {
        "name": "Emerald A-Z Complete All Journals",
        "hosts": ("emerald.com", "emeraldinsight.com", "www.emerald.com"),
        "metadata_terms": ("emerald",),
    },
    {
        "name": "HighWire Press",
        "hosts": ("highwire.org", "highwirepress.com"),
        "metadata_terms": ("highwire",),
    },
    {
        "name": "IEEE Computer Society Digital Library",
        "hosts": ("computer.org", "csdl.computer.org"),
        "metadata_terms": ("ieee computer society",),
    },
    {
        "name": "IOPscience platform",
        "hosts": ("iopscience.iop.org",),
        "metadata_terms": ("iop publishing", "institute of physics"),
    },
    {
        "name": "J-STAGE",
        "hosts": ("jstage.jst.go.jp",),
        "metadata_terms": ("j-stage", "japan science and technology agency"),
        "open_access_only": True,
    },
    {
        "name": "Journals@Ovid Ovid Full Text",
        "hosts": ("ovid.com", "ovidsp.ovid.com"),
        "metadata_terms": ("ovid",),
    },
    {
        "name": "JSTOR Arts & Sciences I-VII Archive Collection",
        "hosts": ("jstor.org", "www.jstor.org"),
        "metadata_terms": ("jstor",),
    },
    {
        "name": "Kinokuniya Digital Library (KinoDen)",
        "hosts": ("kinoden.kinokuniya.co.jp",),
        "metadata_terms": ("kinokuniya", "kinoden"),
    },
    {
        "name": "Maruzen eBook Library",
        "hosts": ("elib.maruzen.co.jp",),
        "metadata_terms": ("maruzen ebook library", "maruzen"),
    },
    {
        "name": "MEDLINE with Full Text",
        "hosts": ("ebscohost.com", "search.ebscohost.com"),
        "metadata_terms": ("medline with full text", "medline"),
        "campus_only": True,
    },
    {
        "name": "National Academy of Sciences (U.S.)",
        "hosts": ("pnas.org", "www.pnas.org"),
        "metadata_terms": ("national academy of sciences", "pnas"),
    },
    {
        "name": "Nature Journals Online",
        "hosts": ("nature.com", "www.nature.com"),
        "metadata_terms": ("springer nature", "nature portfolio"),
    },
    {
        "name": "NII-REO Cambridge University Press",
        "hosts": ("reo.nii.ac.jp", "nii.ac.jp"),
        "metadata_terms": ("nii-reo", "cambridge university press"),
    },
    {
        "name": "NII-REO: OUP Archive Full Collection",
        "hosts": ("reo.nii.ac.jp", "nii.ac.jp"),
        "metadata_terms": ("nii-reo", "oxford university press"),
    },
    {
        "name": "OECD iLibrary",
        "hosts": ("oecd-ilibrary.org", "www.oecd-ilibrary.org"),
        "metadata_terms": ("oecd",),
    },
    {
        "name": "Oxford Journals Full Collection - JUSTICE",
        "hosts": ("academic.oup.com",),
        "metadata_terms": ("oxford university press",),
    },
    {
        "name": "Oxford University Press Books All Titles",
        "hosts": ("academic.oup.com", "oxford.universitypressscholarship.com"),
        "metadata_terms": ("oxford university press",),
    },
    {
        "name": "Project Euclid Prime",
        "hosts": ("projecteuclid.org",),
        "metadata_terms": ("project euclid",),
    },
    {
        "name": "PROLA - Physical Review Online Archive",
        "hosts": ("journals.aps.org", "link.aps.org"),
        "metadata_terms": ("physical review", "american physical society"),
    },
    {
        "name": "ProQuest Central Premium",
        "hosts": ("proquest.com", "www.proquest.com", "search.proquest.com"),
        "metadata_terms": ("proquest",),
    },
    {
        "name": "ProQuest Dissertations & Theses Global",
        "hosts": ("proquest.com", "www.proquest.com", "search.proquest.com"),
        "metadata_terms": ("proquest dissertations", "proquest"),
    },
    {
        "name": "ProQuest Ebook Central",
        "hosts": ("ebookcentral.proquest.com", "proquest.com", "www.proquest.com"),
        "metadata_terms": ("proquest ebook central", "proquest"),
    },
    {
        "name": "PubMed Central",
        "hosts": ("ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov"),
        "metadata_terms": ("pubmed central",),
        "open_access": True,
    },
    {
        "name": "Royal Society of Chemistry",
        "hosts": ("pubs.rsc.org",),
        "metadata_terms": ("royal society of chemistry", "rsc"),
        "campus_only": True,
    },
    {
        "name": "SAGE Journals",
        "hosts": ("journals.sagepub.com",),
        "metadata_terms": ("sage publications", "sage"),
        "campus_only": True,
    },
    {
        "name": "Science Magazine",
        "hosts": ("science.org", "www.science.org"),
        "metadata_terms": ("american association for the advancement of science",),
    },
    {
        "name": "ScienceDirect Freedom Collection",
        "hosts": ("sciencedirect.com", "www.sciencedirect.com"),
        "metadata_terms": ("elsevier",),
    },
    {
        "name": "SCOAP3 Journals",
        "hosts": ("scoap3.org",),
        "metadata_terms": ("scoap3",),
        "open_access": True,
    },
    {
        "name": "Springer Online Journals - JUSTICE",
        "hosts": ("link.springer.com", "springer.com"),
        "metadata_terms": ("springer",),
    },
    {
        "name": "Taylor & Francis Online",
        "hosts": ("tandfonline.com", "www.tandfonline.com"),
        "metadata_terms": ("taylor & francis", "taylor and francis"),
    },
    {
        "name": "Taylor & Francis eBooks A-Z",
        "hosts": ("taylorfrancis.com", "www.taylorfrancis.com"),
        "metadata_terms": ("taylor & francis", "taylor and francis"),
    },
    {
        "name": "U.S. Federal Government Documents (from US GPO)",
        "hosts": ("govinfo.gov", "www.govinfo.gov", "gpo.gov"),
        "metadata_terms": ("u.s. government publishing office", "government publishing office", "gpo"),
        "open_access": True,
    },
    {
        "name": "Web of Science",
        "hosts": ("webofscience.com", "www.webofscience.com"),
        "metadata_terms": ("web of science",),
    },
    {
        "name": "Wiley Online Library Database Model 2026",
        "hosts": ("onlinelibrary.wiley.com",),
        "metadata_terms": ("wiley",),
    },
    {
        "name": "情報学広場：情報処理学会電子図書館",
        "hosts": ("ipsj.ixsq.nii.ac.jp", "ipsj.or.jp"),
        "metadata_terms": ("information processing society of japan", "ipsj", "情報処理学会"),
        "campus_only": True,
    },
    {
        "name": "最新看護索引Web",
        "hosts": ("jamas.or.jp",),
        "metadata_terms": ("最新看護索引", "jamas"),
    },
)
RATE_LIMIT_TERMS = ("too many requests", "rate limit", "suspicious activity", "unusual traffic")
LOGIN_TERMS = (
    "please log in",
    "please login",
    "login required",
    "authentication required",
    "log in to access",
    "login to access",
    "sign in to access",
    "access through your organization",
    "access through another organization",
    "sign in through your institution",
    "sign in via your institution",
    "single sign-on",
    "shibboleth",
    "saml2/redirect/sso",
    "/idp/profile/saml2/",
    "idp.account.",
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
    manual_login_timeout_seconds: int = DEFAULT_MANUAL_LOGIN_TIMEOUT_SECONDS
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


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        href = attr_map.get("href")
        if href:
            self._current_href = href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        text = re.sub(r"\s+", " ", " ".join(self._text_parts)).strip()
        self.anchors.append({"href": self._current_href, "text": text})
        self._current_href = None
        self._text_parts = []


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
            manual_login_timeout_seconds=int(
                payload.get("manual_login_timeout_seconds") or DEFAULT_MANUAL_LOGIN_TIMEOUT_SECONDS
            ),
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
    settings.manual_login_timeout_seconds = max(
        30, min(int(settings.manual_login_timeout_seconds), MAX_MANUAL_LOGIN_TIMEOUT_SECONDS)
    )
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
            "batch_mode": "process_all_deduped_dois",
            "default_batch_size": DEFAULT_MAX_ITEMS,
            "absolute_max_batch_size": ABSOLUTE_MAX_ITEMS,
            "page_action_wait_seconds": [DEFAULT_PAGE_WAIT_MIN, DEFAULT_PAGE_WAIT_MAX],
            "article_delay_seconds": [DEFAULT_ARTICLE_DELAY_MIN, DEFAULT_ARTICLE_DELAY_MAX],
            "fast_mode_default": False,
            "fast_mode_article_delay_seconds": [FAST_ARTICLE_DELAY_MIN, FAST_ARTICLE_DELAY_MAX],
            "fast_mode_max_batch_size": FAST_MAX_ITEMS,
            "manual_access_wait_statuses": sorted(MANUAL_ACCESS_WAIT_STATUSES),
            "manual_login_timeout_seconds": DEFAULT_MANUAL_LOGIN_TIMEOUT_SECONDS,
            "max_manual_login_timeout_seconds": MAX_MANUAL_LOGIN_TIMEOUT_SECONDS,
            "licensed_platform_policy": "try Tsukuba-authorized platforms or open-access resolver candidates only",
            "authorized_platforms": [str(rule["name"]) for rule in SCHOOL_AUTHORIZED_PLATFORM_RULES],
            "campus_only_platforms": [
                str(rule["name"]) for rule in SCHOOL_AUTHORIZED_PLATFORM_RULES if rule.get("campus_only")
            ],
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
        ("blocked_by_captcha", "CAPTCHA, bot check, or security verification detected", CAPTCHA_TERMS, False),
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


def should_wait_for_manual_access(state: str | None, settings: DoiDownloadSettings) -> bool:
    return bool(state in MANUAL_ACCESS_WAIT_STATUSES and settings.allow_manual_login and settings.headed)


def apply_candidate_manual_wait_policy(
    state: str | None,
    reason: str | None,
    diagnostics: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    diagnostics = dict(diagnostics or {})
    candidate = candidate or {}
    platform = candidate.get("authorized_platform") or (candidate.get("policy") or {}).get("platform")
    if state in MANUAL_ACCESS_WAIT_STATUSES and isinstance(platform, dict) and platform.get("campus_only"):
        diagnostics.update(
            {
                "classification": "blocked_by_access",
                "manual_wait_suppressed": True,
                "manual_wait_suppressed_reason": "campus_only_platform",
                "authorized_platform": platform,
            }
        )
        platform_name = platform.get("name") or "该平台"
        return (
            "blocked_by_access",
            f"{platform_name} 在筑波清单中标记为校区内限定；当前仍出现机构登录入口，不继续等待手动登录",
            diagnostics,
        )
    return state, reason, diagnostics


def publisher_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _proxy_target_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    host = publisher_domain(url) or ""
    if host == "tsukuba.idm.oclc.org" and parsed.path.rstrip("/").endswith("/login"):
        target = urllib.parse.parse_qs(parsed.query).get("url", [""])[0]
        return target or None
    return None


def _lower_terms(*values: Any) -> str:
    return " ".join(str(value or "").lower() for value in values)


def _platform_match_by_host(host: str | None) -> dict[str, Any] | None:
    if not host:
        return None
    lowered = host.lower()
    for rule in SCHOOL_AUTHORIZED_PLATFORM_RULES:
        for candidate_host in rule.get("hosts", ()):
            if lowered == candidate_host or lowered.endswith("." + candidate_host):
                return rule
    return None


def _platform_match_by_text(text: str) -> dict[str, Any] | None:
    haystack = text.lower()
    for rule in SCHOOL_AUTHORIZED_PLATFORM_RULES:
        if any(term in haystack for term in rule.get("metadata_terms", ())):
            return rule
    return None


def authorized_platform_for_url(url: str | None) -> dict[str, Any] | None:
    platform = _platform_match_by_host(publisher_domain(url))
    if platform:
        return platform
    return _platform_match_by_host(publisher_domain(_proxy_target_url(url)))


def authorized_platform_for_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    return _platform_match_by_text(
        _lower_terms(
            metadata.get("publisher"),
            metadata.get("journal"),
            metadata.get("container-title"),
            metadata.get("title"),
        )
    )


def _platform_payload(platform: dict[str, Any] | None) -> dict[str, Any] | None:
    if not platform:
        return None
    return {
        "name": platform.get("name"),
        "campus_only": bool(platform.get("campus_only")),
        "open_access": bool(platform.get("open_access")),
        "open_access_only": bool(platform.get("open_access_only")),
    }


def _is_open_access_candidate(candidate: dict[str, Any]) -> bool:
    host = publisher_domain(candidate.get("href")) or ""
    label = str(candidate.get("text") or "").lower()
    source = str(candidate.get("source") or "").lower()
    return (
        _is_public_fulltext_host(host)
        or source in {"hal_api"}
        or any(term in label for term in ("open access", "オープンアクセス", "pubmed central"))
    )


def _candidate_policy(candidate: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    url_platform = authorized_platform_for_url(str(candidate.get("href") or ""))
    metadata_platform = authorized_platform_for_metadata(metadata)
    platform = url_platform or metadata_platform
    open_access = _is_open_access_candidate(candidate)
    allowed = bool(open_access or platform)
    reason = None
    if not allowed:
        reason = "平台不在筑波大学授权数据库列表，且没有开放获取入口"
    return {
        "allowed": allowed,
        "open_access": open_access,
        "platform": _platform_payload(platform),
        "reason": reason,
    }


def _direct_doi_candidate_policy(metadata: dict[str, Any] | None) -> dict[str, Any]:
    platform = authorized_platform_for_metadata(metadata)
    allowed = bool(platform)
    return {
        "allowed": allowed,
        "open_access": False,
        "platform": _platform_payload(platform),
        "reason": None if allowed else "Crossref 出版方不在筑波大学授权数据库列表",
    }


def _annotate_candidate_policy(candidate: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    policy = _candidate_policy(candidate, metadata)
    return {**candidate, "policy": policy, "authorized_platform": policy.get("platform")}


def serials_solutions_lookup_url(doi: str, language: str = "ja") -> str:
    query = urllib.parse.urlencode(
        {
            "SS_LibHash": SERIALS_SOLUTIONS_LIB_HASH,
            "genre": "article",
            "paramdict": language,
            "sid": "sersol:uniqueIDQuery",
            "id": doi,
        }
    )
    return SERIALS_SOLUTIONS_BASE_URL + "?" + query


def _decode_serials_href(href: str) -> str:
    url = urllib.parse.urljoin(SERIALS_SOLUTIONS_BASE_URL, href)
    parsed = urllib.parse.urlparse(url)
    if parsed.path.endswith("/log") or parsed.path.endswith("/log/"):
        target = urllib.parse.parse_qs(parsed.query).get("U", [""])[0]
        if target:
            return target
    return url


def _is_public_fulltext_host(host: str) -> bool:
    return host in LINK_RESOLVER_PUBLIC_HOSTS or host.endswith(".hal.science")


def _prefer_direct_public_url(url: str) -> str:
    target = _proxy_target_url(url)
    if target and _is_public_fulltext_host(publisher_domain(target) or ""):
        return target
    return url


def _link_resolver_candidate_priority(text: str, url: str) -> int | None:
    label = text.lower()
    lowered_url = url.lower()
    host = publisher_domain(url) or ""
    if any(term in label for term in LINK_RESOLVER_SKIP_LABEL_TERMS):
        return None
    if any(term in lowered_url for term in ("refworks.com", "ulrichsweb", "opac", "copy_requests", "ndlsearch")):
        return None
    if "ss_page=refiner" in lowered_url:
        return None
    public_host = _is_public_fulltext_host(host)
    if any(term in text for term in LINK_RESOLVER_FULLTEXT_TERMS) or any(
        term in label for term in LINK_RESOLVER_FULLTEXT_TERMS
    ):
        return 5 if public_host else 10
    if any(term in text for term in LINK_RESOLVER_OPEN_ACCESS_TERMS) or any(
        term in label for term in LINK_RESOLVER_OPEN_ACCESS_TERMS
    ):
        return 15 if public_host else 20
    if public_host:
        return 30
    return None


def parse_serials_solutions_candidates(html_text: str) -> list[dict[str, Any]]:
    collector = _AnchorCollector()
    collector.feed(html_text or "")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in collector.anchors:
        url = _prefer_direct_public_url(_decode_serials_href(anchor["href"]))
        priority = _link_resolver_candidate_priority(anchor.get("text", ""), url)
        if priority is None or url in seen:
            continue
        seen.add(url)
        candidates.append(
            {
                "href": url,
                "text": anchor.get("text", ""),
                "source": "serials_solutions",
                "priority": priority,
                "publisher_domain": publisher_domain(url),
            }
        )
    return sorted(candidates, key=lambda item: item["priority"])


def _hal_id_from_url(url: str) -> str | None:
    match = re.search(r"\bhal-\d+", url.lower())
    return match.group(0) if match else None


def hal_api_lookup_url(hal_url: str) -> str | None:
    hal_id = _hal_id_from_url(hal_url)
    if not hal_id:
        return None
    return "https://api.archives-ouvertes.fr/search/?" + urllib.parse.urlencode(
        {"q": f"halId_s:{hal_id}", "fl": "files_s,uri_s,title_s", "wt": "json"}
    )


def parse_hal_api_file_candidates(candidate: dict[str, Any], json_text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []
    docs = data.get("response", {}).get("docs", [])
    if not docs:
        return []
    files = docs[0].get("files_s") or []
    results: list[dict[str, Any]] = []
    base_priority = int(candidate.get("priority") or 20)
    lookup_url = hal_api_lookup_url(str(candidate.get("href") or ""))
    for href in files:
        if not isinstance(href, str):
            continue
        lowered = href.lower()
        if ".pdf" not in lowered:
            continue
        results.append(
            {
                "href": href,
                "text": f"{candidate.get('text') or 'HAL'} file",
                "source": "hal_api",
                "priority": max(1, base_priority - 1),
                "publisher_domain": publisher_domain(href),
                "lookup_url": lookup_url,
            }
        )
    return results


def fetch_hal_file_candidates(candidate: dict[str, Any], timeout: int = 15) -> list[dict[str, Any]]:
    lookup_url = hal_api_lookup_url(str(candidate.get("href") or ""))
    if not lookup_url:
        return []
    request = urllib.request.Request(
        lookup_url,
        headers={"User-Agent": "personal-research-os-doi-downloader/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - public repository metadata API
            return parse_hal_api_file_candidates(candidate, response.read().decode("utf-8", "replace"))
    except Exception:
        return []


def _expand_public_repository_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        additions = []
        if _hal_id_from_url(str(candidate.get("href") or "")):
            additions = fetch_hal_file_candidates(candidate)
        for item in [*additions, candidate]:
            href = str(item.get("href") or "")
            if not href or href in seen:
                continue
            seen.add(href)
            expanded.append(item)
    return sorted(expanded, key=lambda item: item["priority"])


def fetch_serials_solutions_candidates(doi: str, timeout: int = 25) -> list[dict[str, Any]]:
    url = serials_solutions_lookup_url(doi)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "personal-research-os-doi-downloader/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - configured library link resolver
            html_text = response.read().decode("utf-8", "replace")
    except Exception:
        return []
    candidates = parse_serials_solutions_candidates(html_text)
    for candidate in candidates:
        candidate["lookup_url"] = url
    return _expand_public_repository_candidates(candidates)


def no_authorized_landing_attempt(doi: str, metadata: dict[str, Any] | None = None) -> DownloadAttempt:
    platform = authorized_platform_for_metadata(metadata)
    publisher = str((metadata or {}).get("publisher") or "unknown")
    journal = str((metadata or {}).get("journal") or "")
    diagnostics = {
        "classification": "skipped_not_authorized",
        "publisher": publisher,
        "journal": journal,
        "authorized_platform": _platform_payload(platform),
        "licensed_platform_policy": "try Tsukuba-authorized platforms or open-access resolver candidates only",
    }
    reason = (
        "平台不在筑波大学授权数据库列表，且 Serials Solutions 没有给出开放获取/授权全文入口"
        f" (publisher: {publisher})"
    )
    return DownloadAttempt(
        "skipped_not_authorized",
        f"https://doi.org/{doi}",
        "doi.org",
        None,
        None,
        reason,
        None,
        None,
        diagnostics,
    )


def tsukuba_proxy_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if not host or host.endswith(".tsukuba.idm.oclc.org") or host == "tsukuba.idm.oclc.org":
        return None
    if host not in {"www.sciencedirect.com", "sciencedirect.com", "linkinghub.elsevier.com"}:
        return None
    proxy_host = host.replace(".", "-") + ".tsukuba.idm.oclc.org"
    return urllib.parse.urlunparse((parsed.scheme or "https", proxy_host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _expand_tsukuba_proxy_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        proxy_href = tsukuba_proxy_url(str(candidate.get("href") or ""))
        platform = candidate.get("authorized_platform") or {}
        should_proxy = platform.get("name") in {"Elsevier ScienceDirect Journals", "ScienceDirect Freedom Collection"}
        if proxy_href and should_proxy:
            proxied = {
                **candidate,
                "href": proxy_href,
                "source": f"{candidate.get('source') or 'candidate'}_tsukuba_proxy",
                "publisher_domain": publisher_domain(proxy_href),
                "priority": int(candidate.get("priority") or 50) - 1,
                "proxied_from": candidate.get("href"),
            }
            if proxy_href not in seen:
                expanded.append(proxied)
                seen.add(proxy_href)
        href = str(candidate.get("href") or "")
        if href and href not in seen:
            expanded.append(candidate)
            seen.add(href)
    return expanded


def doi_landing_candidates(
    doi: str, metadata: dict[str, Any] | None = None, *, include_direct: bool = True
) -> list[dict[str, Any]]:
    candidates = [
        annotated
        for candidate in fetch_serials_solutions_candidates(doi)
        for annotated in [_annotate_candidate_policy(candidate, metadata)]
        if annotated["policy"]["allowed"]
    ]
    if include_direct:
        direct_policy = _direct_doi_candidate_policy(metadata)
        if direct_policy["allowed"]:
            direct = {
                "href": f"https://doi.org/{doi}",
                "text": "DOI direct",
                "source": "doi",
                "priority": 100,
                "publisher_domain": "doi.org",
                "policy": direct_policy,
                "authorized_platform": direct_policy.get("platform"),
            }
        else:
            direct = None
        if direct and all(candidate.get("href") != direct["href"] for candidate in candidates):
            candidates.append(direct)
    return _expand_tsukuba_proxy_candidates(candidates)


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
        if payload.get("status") and payload.get("status") != "downloaded":
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
          const blockedPdfTerms = [
            'contentplatform_userguide',
            'userguide',
            'user-guide',
            'wp-content/uploads',
            'manual.pdf',
          ];
          const addLink = (href, text, source) => {
            if (!href) {
              return;
            }
            const lowerHref = href.toLowerCase();
            if (href === '#' ||
                lowerHref.startsWith('javascript:') ||
                lowerHref.startsWith('mailto:') ||
                lowerHref.startsWith('tel:') ||
                blockedPdfTerms.some((term) => lowerHref.includes(term))) {
              return;
            }
            if (!links.some((link) => link.href === href)) {
              links.push({ href, text, source });
            }
          };
          try {
            const state = window.__PRELOADED_STATE__ || {};
            const article = state.article || {};
            const metadata = article.pdfDownload && article.pdfDownload.urlMetadata;
            if (metadata && metadata.path && metadata.pii && metadata.pdfExtension) {
              const url = new URL(`/${metadata.path}/${metadata.pii}${metadata.pdfExtension}`, location.origin);
              Object.entries(metadata.queryParams || {}).forEach(([key, value]) => {
                if (value !== undefined && value !== null) {
                  url.searchParams.set(key, String(value));
                }
              });
              addLink(url.href, 'sciencedirect_pdf_download', 'sciencedirect_state');
            }
            if (article.openManuscriptUrl) {
              addLink(new URL(article.openManuscriptUrl, location.origin).href, 'sciencedirect_open_manuscript', 'sciencedirect_state');
            }
          } catch (_error) {
          }
          const currentUrl = new URL(location.href);
          const currentLower = location.href.toLowerCase();
          if (currentLower.endsWith('.pdf') || currentLower.includes('/pdf') ||
              (currentUrl.hostname.endsWith('hal.science') && currentUrl.pathname.endsWith('/document'))) {
            addLink(location.href, 'current_pdf_like_url', 'current_url');
          }
          document.querySelectorAll('meta').forEach((meta) => {
            const name = (meta.getAttribute('name') || meta.getAttribute('property') || '').toLowerCase();
            const content = meta.getAttribute('content') || '';
            const lowerContent = content.toLowerCase();
            if (content && name.includes('citation_pdf_url') &&
                !lowerContent.startsWith('javascript:') &&
                !lowerContent.startsWith('mailto:') &&
                !lowerContent.startsWith('tel:') &&
                !blockedPdfTerms.some((term) => lowerContent.includes(term))) {
              addLink(content, 'citation_pdf_url', 'meta');
            }
          });
          document.querySelectorAll('a[href]').forEach((a) => {
            const href = a.href;
            const text = (a.textContent || a.getAttribute('aria-label') || '').trim();
            const lowerHref = href.toLowerCase();
            const lowerText = text.toLowerCase();
            if (!href || href === '#' ||
                lowerHref.startsWith('javascript:') ||
                lowerHref.startsWith('mailto:') ||
                lowerHref.startsWith('tel:') ||
                blockedPdfTerms.some((term) => lowerHref.includes(term))) {
              return;
            }
            if (lowerHref.endsWith('.pdf') || lowerHref.includes('/pdf') ||
                lowerText === 'pdf' || lowerText.includes('download pdf') ||
                lowerText.includes('article pdf') || lowerText.includes('view pdf')) {
              addLink(href, text, 'link');
            }
          });
          const ieeeMatch = location.href.match(/ieeexplore\\.ieee\\.org\\/document\\/(\\d+)/i);
          if (ieeeMatch) {
            addLink(
              `https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=${ieeeMatch[1]}`,
              'ieee_stamp_pdf',
              'publisher_fallback'
            );
          }
          return links;
        }
        """
    )


def _decode_json_string_fragment(value: str) -> str:
    try:
        return str(json.loads(f'"{value}"'))
    except Exception:
        return value.replace("\\/", "/")


def _append_unique_pdf_link(
    links: list[dict[str, str]], href: str | None, text: str, source: str, base_url: str | None
) -> None:
    if not href:
        return
    absolute = urllib.parse.urljoin(base_url or "", href)
    lowered = absolute.lower()
    if (
        not absolute
        or absolute == "#"
        or lowered.startswith("javascript:")
        or lowered.startswith("mailto:")
        or lowered.startswith("tel:")
        or "contentplatform_userguide" in lowered
        or "wp-content/uploads" in lowered
    ):
        return
    if all(link.get("href") != absolute for link in links):
        links.append({"href": absolute, "text": text, "source": source})


def pdf_links_from_html_snapshot(html: str | None, base_url: str | None = None) -> list[dict[str, str]]:
    if not html:
        return []
    links: list[dict[str, str]] = []
    manuscript_match = re.search(r'"openManuscriptUrl"\s*:\s*"([^"]+)"', html)
    if manuscript_match:
        _append_unique_pdf_link(
            links,
            _decode_json_string_fragment(manuscript_match.group(1)),
            "sciencedirect_open_manuscript",
            "sciencedirect_html_state",
            base_url,
        )
    pdf_index = html.find('"pdfDownload"')
    if pdf_index >= 0:
        section = html[pdf_index : pdf_index + 2500]
        values = {
            key: _decode_json_string_fragment(match.group(1))
            for key in ("path", "pii", "pdfExtension", "md5", "pid")
            for match in [re.search(rf'"{key}"\s*:\s*"([^"]+)"', section)]
            if match
        }
        if values.get("path") and values.get("pii") and values.get("pdfExtension"):
            pdf_url = urllib.parse.urljoin(
                base_url or "",
                f"/{values['path'].strip('/')}/{values['pii']}{values['pdfExtension']}",
            )
            query = {
                key: value
                for key, value in (("md5", values.get("md5")), ("pid", values.get("pid")))
                if value
            }
            if query:
                separator = "&" if urllib.parse.urlparse(pdf_url).query else "?"
                pdf_url = pdf_url + separator + urllib.parse.urlencode(query)
            _append_unique_pdf_link(
                links,
                pdf_url,
                "sciencedirect_pdf_download",
                "sciencedirect_html_state",
                base_url,
            )
    return links


def merge_pdf_links(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for group in groups:
        for link in group:
            _append_unique_pdf_link(merged, link.get("href"), link.get("text", ""), link.get("source", ""), None)
    return merged


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

    def _download_from_target(
        self,
        page: Any,
        doi: str,
        metadata: dict[str, Any],
        artifacts_dir: Path,
        candidate: dict[str, Any],
    ) -> DownloadAttempt:
        landing_url = None
        target_url = str(candidate.get("href") or f"https://doi.org/{doi}")
        try:
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
            diagnostics = {**diagnostics, "landing_candidate": candidate}
            state, reason, diagnostics = apply_candidate_manual_wait_policy(state, reason, diagnostics, candidate)
            links = merge_pdf_links(_pdf_links_from_page(page), pdf_links_from_html_snapshot(page.content(), page.url))
            defer_manual_wait = bool(state in {"needs_login", "blocked_by_access"} and links)
            if defer_manual_wait:
                diagnostics = {
                    **diagnostics,
                    "manual_access_wait_deferred_for_pdf_links": True,
                    "pdf_link_count_before_manual_wait": len(links),
                }
            elif should_wait_for_manual_access(state, self.settings):
                deadline = time.monotonic() + self.settings.manual_login_timeout_seconds
                waited_seconds = 0.0
                while time.monotonic() < deadline:
                    before_sleep = time.monotonic()
                    time.sleep(2)
                    waited_seconds += time.monotonic() - before_sleep
                    state, reason, diagnostics = _classify_access_block_detail(None, page.url, self._body_text(page))
                    diagnostics = {
                        **diagnostics,
                        "manual_access_waited": True,
                        "manual_access_wait_seconds": round(waited_seconds, 1),
                        "manual_access_wait_statuses": sorted(MANUAL_ACCESS_WAIT_STATUSES),
                        "landing_candidate": candidate,
                    }
                    state, reason, diagnostics = apply_candidate_manual_wait_policy(state, reason, diagnostics, candidate)
                    if state not in MANUAL_ACCESS_WAIT_STATUSES:
                        break
            if state and state != "blocked_by_access" and not defer_manual_wait:
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

    def download(self, doi: str, metadata: dict[str, Any], artifacts_dir: Path) -> DownloadAttempt:
        assert self.context is not None
        page = self.context.new_page()
        last_attempt: DownloadAttempt | None = None
        try:
            candidates = doi_landing_candidates(doi, metadata)
            if not candidates:
                return no_authorized_landing_attempt(doi, metadata)
            for candidate in candidates:
                attempt = self._download_from_target(page, doi, metadata, artifacts_dir, candidate)
                if attempt.status == "downloaded":
                    return attempt
                last_attempt = attempt
                if attempt.status in STOP_BATCH_STATUSES:
                    return attempt
            if last_attempt:
                return last_attempt
            return DownloadAttempt("failed", None, None, None, None, "No DOI landing candidate found")
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


def _batch_count(total_items: int, batch_size: int) -> int:
    if total_items <= 0:
        return 0
    return (total_items + batch_size - 1) // batch_size


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
    batch_size = resolved.max_items
    total_batches = _batch_count(len(all_dois), batch_size)
    job_id = _create_job(all_dois, resolved, len(all_dois))
    out_dir = Path(resolved.out_dir).expanduser()
    artifacts_dir = SNAPSHOT_DIR / job_id
    items: list[dict[str, Any]] = []
    stop_reason = None

    if not all_dois:
        summary = {
            "status_counts": {"failed": 0},
            "message": "No valid DOI supplied",
            "input_count": len(all_dois),
            "requested_count": 0,
            "processed_count": 0,
            "unprocessed_count": 0,
            "batch_size": batch_size,
            "batch_count": 0,
            "completed_batches": 0,
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
        for idx, doi in enumerate(all_dois, start=1):
            batch_index = (idx - 1) // batch_size + 1
            batch_item_index = (idx - 1) % batch_size + 1
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
                items.append(
                    {
                        "id": item_id,
                        "doi": doi,
                        "status": "skipped_existing",
                        "batch_index": batch_index,
                        "batch_item_index": batch_item_index,
                        **existing,
                    }
                )
                continue

            if browser_runner is None and find_spec("playwright") is None:
                reason = "Playwright is not installed; run python -m pip install playwright and python -m playwright install chromium"
                _update_item(item_id, status="failed", failure_reason=reason)
                items.append(
                    {
                        "id": item_id,
                        "doi": doi,
                        "status": "failed",
                        "failure_reason": reason,
                        "batch_index": batch_index,
                        "batch_item_index": batch_item_index,
                    }
                )
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
                            "batch_index": batch_index,
                            "batch_item_index": batch_item_index,
                        }
                    )
                except Exception as exc:
                    _update_item(item_id, status="failed", failure_reason=str(exc))
                    items.append(
                        {
                            "id": item_id,
                            "doi": doi,
                            "status": "failed",
                            "failure_reason": str(exc),
                            "batch_index": batch_index,
                            "batch_item_index": batch_item_index,
                        }
                    )
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
                        "batch_index": batch_index,
                        "batch_item_index": batch_item_index,
                    }
                )
                if attempt.status in STOP_BATCH_STATUSES:
                    stop_reason = attempt.failure_reason or attempt.status
                    break

            if idx < len(all_dois) and not stop_reason:
                _sleep_article(resolved, sleeper)

    processed_count = len(items)
    for idx in range(processed_count + 1, len(all_dois) + 1):
        doi = all_dois[idx - 1]
        batch_index = (idx - 1) // batch_size + 1
        batch_item_index = (idx - 1) % batch_size + 1
        item_id = _create_item(job_id, doi)
        _update_item(
            item_id,
            status="pending",
            failure_reason=f"Not processed because the job stopped: {stop_reason}",
        )
        items.append(
            {
                "id": item_id,
                "doi": doi,
                "status": "pending",
                "failure_reason": f"Not processed because the job stopped: {stop_reason}",
                "batch_index": batch_index,
                "batch_item_index": batch_item_index,
            }
        )

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
        "requested_count": len(all_dois),
        "processed_count": processed_count,
        "unprocessed_count": max(0, len(all_dois) - processed_count),
        "batch_size": batch_size,
        "batch_count": total_batches,
        "completed_batches": _batch_count(processed_count, batch_size),
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
