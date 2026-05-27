from __future__ import annotations

import csv
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from . import config
from .db import connect, init_db, json_dumps, json_loads, utc_now
from .literature_discovery import Fetcher, search_openalex, split_terms

VALID_STATUSES = {"new", "maybe_relevant", "highly_relevant", "must_read", "read", "archived"}
DEFAULT_PROFILE = "gaa_vertical_ge_si"
CONFIG_DIR = config.PROJECT_ROOT / "config"
JOURNALS_PATH = CONFIG_DIR / "acs_journals.json"
PROFILES_PATH = CONFIG_DIR / "acs_profiles.json"
EXPORT_DIR = config.OUTPUT_DIR / "acs_literature"

CSV_FIELDS = [
    "status",
    "relevance_score",
    "title",
    "authors",
    "journal",
    "year",
    "publication_date",
    "doi",
    "url",
    "matched_keywords",
    "next_action",
    "notes",
]


def default_journals() -> list[dict[str, Any]]:
    return [
        {
            "name": "ACS Nano",
            "terms": ["ACS Nano", "1936-0851"],
            "enabled": True,
            "high_value": True,
        },
        {
            "name": "Nano Letters",
            "terms": ["Nano Letters", "1530-6984"],
            "enabled": True,
            "high_value": True,
        },
        {
            "name": "Chemistry of Materials",
            "terms": ["Chemistry of Materials", "0897-4756"],
            "enabled": True,
            "high_value": False,
        },
        {
            "name": "ACS Applied Materials & Interfaces",
            "terms": ["ACS Applied Materials & Interfaces", "1944-8244"],
            "enabled": True,
            "high_value": False,
        },
    ]


def default_profiles() -> list[dict[str, Any]]:
    return [
        {
            "name": DEFAULT_PROFILE,
            "topic": "semiconductor device fabrication materials",
            "search_query": "semiconductor device",
            "search_keywords": ["fabrication", "materials"],
            "include_keywords": [
                "semiconductor",
                "device",
                "fabrication",
                "materials",
                "interface",
                "high-k",
            ],
            "strong_keywords": [
                "semiconductor",
                "device",
                "fabrication",
            ],
            "strong_combinations": [
                ["device", "fabrication"],
                ["materials", "interface"],
            ],
            "journal_terms": ["ACS Nano", "Nano Letters", "Chemistry of Materials", "ACS Applied Materials"],
            "year_from": 2023,
            "max_results": 12,
        }
    ]


def ensure_default_configs(*, force: bool = False) -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    writes = []
    if force or not JOURNALS_PATH.exists():
        JOURNALS_PATH.write_text(json.dumps({"journals": default_journals()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        writes.append(str(JOURNALS_PATH))
    if force or not PROFILES_PATH.exists():
        PROFILES_PATH.write_text(json.dumps({"profiles": default_profiles()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        writes.append(str(PROFILES_PATH))
    return {
        "status": "ready",
        "journals_path": str(JOURNALS_PATH),
        "profiles_path": str(PROFILES_PATH),
        "written": writes,
    }


def _read_json(path: Path, key: str, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not path.exists():
        return fallback
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(value, list):
        raise ValueError(f"{path} must contain a JSON list at {key}")
    return [item for item in value if isinstance(item, dict)]


def load_journals(path: Path = JOURNALS_PATH) -> list[dict[str, Any]]:
    return _read_json(path, "journals", default_journals())


def load_profiles(path: Path = PROFILES_PATH) -> list[dict[str, Any]]:
    return _read_json(path, "profiles", default_profiles())


def get_profile(profile_name: str) -> dict[str, Any]:
    for profile in load_profiles():
        if profile.get("name") == profile_name:
            return profile
    available = ", ".join(profile.get("name", "unnamed") for profile in load_profiles())
    raise ValueError(f"Unknown ACS profile '{profile_name}'. Available profiles: {available}")


def normalize_doi(value: Any) -> str:
    doi = str(value or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    return doi.strip().lower()


def paper_key_for(*, doi: Any = None, title: Any = "", url: Any = "") -> str:
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        return "doi:" + normalized_doi
    seed = re.sub(r"\s+", " ", f"{title or ''} {url or ''}".strip().lower())
    return "title_url:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _contains(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def _unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _journal_is_high_value(journal_name: str, journals: list[dict[str, Any]]) -> bool:
    for journal in journals:
        if not journal.get("enabled", True) or not journal.get("high_value"):
            continue
        terms = [journal.get("name", ""), *journal.get("terms", [])]
        if any(term and _contains(journal_name, str(term)) for term in terms):
            return True
    return False


def score_candidate(item: dict[str, Any], profile: dict[str, Any], journals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    title = str(item.get("title") or "")
    abstract = str(item.get("abstract_en") or item.get("abstract") or "")
    combined = f"{title}\n{abstract}"
    score = 0.0
    matches: list[str] = []
    reasons: list[str] = []

    for keyword in split_terms(profile.get("strong_keywords") or []):
        title_hit = _contains(title, keyword)
        abstract_hit = _contains(abstract, keyword)
        if title_hit:
            score += 3
            matches.append(keyword)
            reasons.append(f"title strong keyword: {keyword}")
        if abstract_hit:
            score += 2
            matches.append(keyword)
            reasons.append(f"abstract strong keyword: {keyword}")

    for keyword in split_terms(profile.get("include_keywords") or []):
        title_hit = _contains(title, keyword)
        abstract_hit = _contains(abstract, keyword)
        if title_hit:
            score += 1
            matches.append(keyword)
            reasons.append(f"title keyword: {keyword}")
        if abstract_hit:
            score += 0.5
            matches.append(keyword)
            reasons.append(f"abstract keyword: {keyword}")

    for combo in profile.get("strong_combinations") or []:
        terms = [str(term).strip() for term in combo if str(term).strip()]
        if terms and all(_contains(combined, term) for term in terms):
            score += 4
            label = " + ".join(terms)
            matches.append(label)
            reasons.append(f"strong combination: {label}")

    if journals and _journal_is_high_value(str(item.get("journal") or ""), journals):
        score += 1
        reasons.append("high value target journal")

    return {
        "score": round(score, 2),
        "matched_keywords": _unique(matches),
        "reasons": reasons,
        "next_action": suggest_next_action(score),
    }


def suggest_next_action(score: float) -> str:
    if score >= 8:
        return "优先加入 must_read，检查摘要和 DOI 后决定是否下载全文。"
    if score >= 4:
        return "放入 maybe_relevant，人工快速筛选是否进入阅读队列。"
    return "低优先级暂存；除非研究方向变化，否则先不投入下载和阅读时间。"


def _enabled_journal_terms(journals: list[dict[str, Any]], profile: dict[str, Any]) -> list[str]:
    profile_terms = split_terms(profile.get("journal_terms") or [])
    if profile_terms:
        return profile_terms
    terms: list[str] = []
    for journal in journals:
        if not journal.get("enabled", True):
            continue
        terms.append(str(journal.get("name") or ""))
        terms.extend(str(term) for term in journal.get("terms") or [])
    return [term for term in _unique(terms) if term]


def _paper_from_item(item: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(item.get("doi"))
    url = item.get("landing_url") or item.get("source_url") or item.get("url") or ""
    title = item.get("title") or "Untitled ACS candidate"
    return {
        "paper_key": paper_key_for(doi=doi, title=title, url=url),
        "doi": doi,
        "title": title,
        "authors": item.get("authors") or [],
        "journal": item.get("journal") or "",
        "year": item.get("year"),
        "publication_date": item.get("publication_date") or "",
        "url": url,
        "abstract": item.get("abstract_en") or item.get("abstract") or "",
        "source": item.get("source") or "openalex",
        "relevance_score": score["score"],
        "matched_keywords": score["matched_keywords"],
        "next_action": score["next_action"],
        "metadata": {
            "openalex_id": item.get("openalex_id"),
            "pdf_url": item.get("pdf_url") or "",
            "score_reasons": score["reasons"],
            "source_record": item,
        },
    }


def run_tracker(
    *,
    profile_name: str = DEFAULT_PROFILE,
    max_results: int | None = None,
    year_from: Any = None,
    year_to: Any = None,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    ensure_default_configs()
    init_db()
    profile = get_profile(profile_name)
    journals = load_journals()
    journal_terms = _enabled_journal_terms(journals, profile)
    scoring_terms = _unique(split_terms(profile.get("strong_keywords") or []) + split_terms(profile.get("include_keywords") or []))
    search_terms = split_terms(profile.get("search_keywords") or [])
    search_query = str(profile.get("search_query") or profile.get("topic") or profile_name)
    limit = max_results or int(profile.get("max_results") or 12)
    started_at = utc_now()
    run_id = "acsrun_" + uuid.uuid4().hex[:16]
    settings = {
        "profile_name": profile_name,
        "topic": profile.get("topic") or profile_name,
        "search_query": search_query,
        "search_terms": search_terms,
        "scoring_terms": scoring_terms,
        "journal_terms": journal_terms,
        "year_from": year_from or profile.get("year_from"),
        "year_to": year_to or profile.get("year_to"),
        "max_results": limit,
        "metadata_source": "openalex",
    }

    with connect() as con:
        con.execute(
            """
            INSERT INTO acs_literature_runs (run_id, profile_name, status, settings_json, started_at)
            VALUES (?, ?, 'running', ?, ?)
            """,
            (run_id, profile_name, json_dumps(settings), started_at),
        )

    try:
        results, meta = search_openalex(
            query=search_query,
            keywords=search_terms,
            journals=journal_terms,
            year_from=year_from or profile.get("year_from"),
            year_to=year_to or profile.get("year_to"),
            max_results=limit,
            fetcher=fetcher,
        )
        now = utc_now()
        created = 0
        updated = 0
        papers = []
        with connect() as con:
            for rank, item in enumerate(results, start=1):
                scored = score_candidate(item, profile, journals)
                paper = _paper_from_item(item, scored)
                existing = con.execute(
                    "SELECT paper_key FROM acs_literature_papers WHERE paper_key=?",
                    (paper["paper_key"],),
                ).fetchone()
                if existing:
                    updated += 1
                else:
                    created += 1
                con.execute(
                    """
                    INSERT INTO acs_literature_papers (
                      paper_key, doi, title, authors_json, journal, year, publication_date,
                      url, abstract, source, collected_at, first_seen_at, last_seen_at,
                      relevance_score, status, notes, matched_keywords_json, next_action, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL, ?, ?, ?)
                    ON CONFLICT(paper_key) DO UPDATE SET
                      title=excluded.title,
                      authors_json=excluded.authors_json,
                      journal=excluded.journal,
                      year=excluded.year,
                      publication_date=excluded.publication_date,
                      url=excluded.url,
                      abstract=excluded.abstract,
                      source=excluded.source,
                      collected_at=excluded.collected_at,
                      last_seen_at=excluded.last_seen_at,
                      relevance_score=excluded.relevance_score,
                      matched_keywords_json=excluded.matched_keywords_json,
                      next_action=excluded.next_action,
                      metadata_json=excluded.metadata_json
                    """,
                    (
                        paper["paper_key"],
                        paper["doi"],
                        paper["title"],
                        json_dumps(paper["authors"]),
                        paper["journal"],
                        paper["year"],
                        paper["publication_date"],
                        paper["url"],
                        paper["abstract"],
                        paper["source"],
                        now,
                        now,
                        now,
                        paper["relevance_score"],
                        json_dumps(paper["matched_keywords"]),
                        paper["next_action"],
                        json_dumps(paper["metadata"]),
                    ),
                )
                con.execute(
                    """
                    INSERT INTO acs_literature_run_items (
                      run_id, paper_key, result_rank, relevance_score, matched_keywords_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, paper_key) DO UPDATE SET
                      result_rank=excluded.result_rank,
                      relevance_score=excluded.relevance_score,
                      matched_keywords_json=excluded.matched_keywords_json
                    """,
                    (
                        run_id,
                        paper["paper_key"],
                        rank,
                        paper["relevance_score"],
                        json_dumps(paper["matched_keywords"]),
                    ),
                )
                papers.append(paper)
            summary = {
                "created": created,
                "updated": updated,
                "result_count": len(papers),
                "openalex": meta,
            }
            con.execute(
                """
                UPDATE acs_literature_runs
                SET status='ready', summary_json=?, finished_at=?
                WHERE run_id=?
                """,
                (json_dumps(summary), utc_now(), run_id),
            )
        return {"status": "ready", "run_id": run_id, "summary": summary, "papers": papers}
    except Exception as exc:
        with connect() as con:
            con.execute(
                """
                UPDATE acs_literature_runs
                SET status='failed', failure_reason=?, finished_at=?
                WHERE run_id=?
                """,
                (str(exc), utc_now(), run_id),
            )
        raise


def _row_to_paper(row: Any) -> dict[str, Any]:
    paper = dict(row)
    paper["authors"] = json_loads(paper.pop("authors_json", "[]"), [])
    paper["matched_keywords"] = json_loads(paper.pop("matched_keywords_json", "[]"), [])
    paper["metadata"] = json_loads(paper.pop("metadata_json", "{}"), {})
    return paper


def list_papers(*, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT *
            FROM acs_literature_papers
            {where}
            ORDER BY
              CASE status
                WHEN 'must_read' THEN 0
                WHEN 'highly_relevant' THEN 1
                WHEN 'maybe_relevant' THEN 2
                WHEN 'new' THEN 3
                WHEN 'read' THEN 4
                ELSE 5
              END,
              relevance_score DESC,
              publication_date DESC,
              last_seen_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_paper(row) for row in rows]


def tracker_status() -> dict[str, Any]:
    init_db()
    with connect() as con:
        counts = {
            row["status"]: row["n"]
            for row in con.execute(
                "SELECT status, COUNT(*) AS n FROM acs_literature_papers GROUP BY status ORDER BY status"
            ).fetchall()
        }
        runs = []
        for row in con.execute(
            """
            SELECT run_id, profile_name, status, settings_json, summary_json,
                   failure_reason, started_at, finished_at
            FROM acs_literature_runs
            ORDER BY started_at DESC
            LIMIT 5
            """
        ).fetchall():
            run = dict(row)
            run["settings"] = json_loads(run.pop("settings_json"), {})
            run["summary"] = json_loads(run.pop("summary_json"), {})
            runs.append(run)
    return {"status": "ready", "counts": counts, "recent_runs": runs, "total": sum(counts.values())}


def mark_paper(*, status: str, doi: str | None = None, paper_key: str | None = None, notes: str | None = None) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
    if not doi and not paper_key:
        raise ValueError("Provide --doi or --paper-key")
    init_db()
    where = "paper_key=?"
    value = paper_key
    if doi:
        where = "doi=?"
        value = normalize_doi(doi)
    with connect() as con:
        row = con.execute(f"SELECT paper_key FROM acs_literature_papers WHERE {where}", (value,)).fetchone()
        if not row:
            raise ValueError("ACS paper not found")
        if notes is None:
            con.execute(
                "UPDATE acs_literature_papers SET status=?, last_seen_at=? WHERE paper_key=?",
                (status, utc_now(), row["paper_key"]),
            )
        else:
            con.execute(
                "UPDATE acs_literature_papers SET status=?, notes=?, last_seen_at=? WHERE paper_key=?",
                (status, notes, utc_now(), row["paper_key"]),
            )
    return {"status": "ready", "paper_key": row["paper_key"], "paper_status": status}


def _digest_group(paper: dict[str, Any]) -> str:
    if paper["status"] == "archived":
        return "Archived"
    if paper["status"] == "read":
        return "Read"
    if paper["status"] == "must_read" or paper["relevance_score"] >= 8:
        return "Must Read Candidates"
    if paper["status"] in {"highly_relevant", "maybe_relevant"} or paper["relevance_score"] >= 4:
        return "Maybe Relevant"
    return "Low Priority"


def render_markdown_digest(*, profile_name: str | None = None, papers: list[dict[str, Any]] | None = None) -> str:
    papers = papers if papers is not None else list_papers(limit=100)
    title_profile = profile_name or "all_profiles"
    lines = [
        "# ACS Literature Digest",
        "",
        f"Generated at: {utc_now()}",
        f"Profile: {title_profile}",
        "",
    ]
    groups = ["Must Read Candidates", "Maybe Relevant", "Low Priority", "Read", "Archived"]
    grouped = {name: [] for name in groups}
    for paper in papers:
        grouped[_digest_group(paper)].append(paper)
    for group in groups:
        lines.append(f"## {group}")
        lines.append("")
        if not grouped[group]:
            lines.append("_No papers._")
            lines.append("")
            continue
        for index, paper in enumerate(grouped[group], start=1):
            doi = paper.get("doi") or "no DOI"
            authors = ", ".join(paper.get("authors") or [])
            matched = ", ".join(paper.get("matched_keywords") or [])
            lines.extend(
                [
                    f"### {index}. {paper['title']}",
                    f"- Journal: {paper.get('journal') or 'Unknown'}",
                    f"- Year: {paper.get('year') or 'Unknown'}",
                    f"- DOI: {doi}",
                    f"- URL: {paper.get('url') or 'N/A'}",
                    f"- Score: {paper.get('relevance_score')}",
                    f"- Status: {paper.get('status')}",
                    f"- Authors: {authors or 'Unknown'}",
                    f"- Matched keywords: {matched or 'None'}",
                    f"- Suggested next action: {paper.get('next_action') or ''}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def export_digest(
    *,
    output_format: str = "markdown",
    profile_name: str | None = None,
    output_path: str | Path | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    init_db()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    papers = list_papers(limit=limit)
    stamp = utc_now().replace(":", "").replace("+", "_")
    if output_format == "markdown":
        path = Path(output_path) if output_path else EXPORT_DIR / f"acs_digest_{stamp}.md"
        content = render_markdown_digest(profile_name=profile_name, papers=papers)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    elif output_format == "csv":
        path = Path(output_path) if output_path else EXPORT_DIR / f"acs_papers_{stamp}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for paper in papers:
                writer.writerow(
                    {
                        "status": paper.get("status"),
                        "relevance_score": paper.get("relevance_score"),
                        "title": paper.get("title"),
                        "authors": "; ".join(paper.get("authors") or []),
                        "journal": paper.get("journal"),
                        "year": paper.get("year"),
                        "publication_date": paper.get("publication_date"),
                        "doi": paper.get("doi"),
                        "url": paper.get("url"),
                        "matched_keywords": "; ".join(paper.get("matched_keywords") or []),
                        "next_action": paper.get("next_action"),
                        "notes": paper.get("notes") or "",
                    }
                )
    else:
        raise ValueError("output_format must be markdown or csv")
    return {"status": "ready", "format": output_format, "file_path": str(path), "count": len(papers)}
