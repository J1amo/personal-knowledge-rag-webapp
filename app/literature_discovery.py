from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
VALID_LANGUAGE_MODES = {"bilingual", "zh", "en"}

Fetcher = Callable[[str], dict[str, Any]]
Translator = Callable[[str], str | dict[str, Any]]


def split_terms(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_parts = [str(item) for item in value]
    else:
        raw_parts = str(value).replace("\n", ",").replace(";", ",").split(",")
    return [part.strip() for part in raw_parts if part and part.strip()]


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _year(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1600 <= number <= 2200 else None


def abstract_from_inverted_index(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    words: list[tuple[int, str]] = []
    for token, positions in index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            try:
                words.append((int(pos), str(token)))
            except (TypeError, ValueError):
                continue
    return " ".join(token for _pos, token in sorted(words)).strip()


def _doi_from_openalex(value: Any) -> str:
    doi = str(value or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    return doi.strip()


def _authors(work: dict[str, Any], limit: int = 8) -> list[str]:
    names: list[str] = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        name = (author.get("display_name") or "").strip()
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def normalize_openalex_work(work: dict[str, Any]) -> dict[str, Any]:
    primary_location = work.get("primary_location") or {}
    best_oa_location = work.get("best_oa_location") or {}
    source = primary_location.get("source") or {}
    abstract = work.get("abstract") or abstract_from_inverted_index(work.get("abstract_inverted_index"))
    landing_url = primary_location.get("landing_page_url") or best_oa_location.get("landing_page_url") or ""
    pdf_url = primary_location.get("pdf_url") or best_oa_location.get("pdf_url") or ""
    return {
        "openalex_id": work.get("id") or "",
        "title": work.get("title") or work.get("display_name") or "Untitled work",
        "doi": _doi_from_openalex(work.get("doi")),
        "abstract_en": abstract or "",
        "abstract_zh": "",
        "abstract_display": abstract or "",
        "journal": source.get("display_name") or "",
        "issn_l": source.get("issn_l") or "",
        "issn": source.get("issn") or [],
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date") or "",
        "authors": _authors(work),
        "cited_by_count": work.get("cited_by_count") or 0,
        "language": work.get("language") or "",
        "type": work.get("type") or "",
        "is_retracted": bool(work.get("is_retracted")),
        "landing_url": landing_url,
        "pdf_url": pdf_url,
        "source_url": landing_url or work.get("id") or "",
        "source": "openalex",
        "translation_status": "not_requested",
        "translation_error": None,
    }


def _matches_journal(item: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return True
    haystack = " ".join(
        [
            str(item.get("journal") or ""),
            str(item.get("issn_l") or ""),
            " ".join(str(value) for value in item.get("issn") or []),
        ]
    ).lower()
    return any(term.lower() in haystack for term in terms)


def _openalex_fetch(url: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "PersonalResearchOS/0.1 (local user research assistant)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - public metadata endpoint
        return json.loads(response.read().decode("utf-8"))


def search_openalex(
    *,
    query: str,
    keywords: str | list[str] | tuple[str, ...] | None = None,
    journals: str | list[str] | tuple[str, ...] | None = None,
    year_from: Any = None,
    year_to: Any = None,
    max_results: Any = 10,
    fetcher: Fetcher | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    keyword_terms = split_terms(keywords)
    journal_terms = split_terms(journals)
    limit = _safe_int(max_results, default=10, minimum=1, maximum=20)
    request_size = min(100, max(limit * 4, 25))
    search_text = " ".join([query.strip(), *keyword_terms]).strip()
    if not search_text:
        raise ValueError("query is required")

    filters = ["has_doi:true", "has_abstract:true", "type:article"]
    start_year = _year(year_from)
    end_year = _year(year_to)
    if start_year:
        filters.append(f"from_publication_date:{start_year}-01-01")
    if end_year:
        filters.append(f"to_publication_date:{end_year}-12-31")

    params = {
        "search": search_text,
        "filter": ",".join(filters),
        "sort": "relevance_score:desc",
        "per_page": str(request_size),
        "select": ",".join(
            [
                "id",
                "doi",
                "title",
                "display_name",
                "publication_year",
                "publication_date",
                "type",
                "language",
                "cited_by_count",
                "is_retracted",
                "primary_location",
                "best_oa_location",
                "authorships",
                "abstract_inverted_index",
            ]
        ),
    }
    api_key = os.getenv("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key
    mailto = os.getenv("OPENALEX_MAILTO")
    if mailto:
        params["mailto"] = mailto

    url = OPENALEX_WORKS_URL + "?" + urllib.parse.urlencode(params)
    payload = fetcher(url) if fetcher else _openalex_fetch(url)
    raw_results = payload.get("results") or []
    normalized = [normalize_openalex_work(work) for work in raw_results]
    filtered = [item for item in normalized if _matches_journal(item, journal_terms)]
    return filtered[:limit], {
        "source": "openalex",
        "request_url": url,
        "requested": request_size,
        "raw_count": len(raw_results),
        "filtered_count": len(filtered),
        "journal_terms": journal_terms,
        "meta": payload.get("meta") or {},
    }


def _translation_settings() -> dict[str, Any]:
    base_url = (
        os.getenv("TRANSLATION_LLM_BASE_URL")
        or os.getenv("HYMT2_OPENAI_BASE_URL")
        or os.getenv("LOCAL_TRANSLATION_BASE_URL")
    )
    model = os.getenv("TRANSLATION_LLM_MODEL") or os.getenv("HYMT2_MODEL") or "hy-mt2"
    api_key = os.getenv("TRANSLATION_LLM_API_KEY") or os.getenv("HYMT2_OPENAI_API_KEY")
    return {
        "backend": "local_translation_llm",
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "api_key_configured": bool(api_key),
    }


def _chat_completion(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str | None = None,
    timeout: int = 90,
    max_tokens: int | None = None,
) -> tuple[str, dict[str, Any]]:
    payload_dict: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1}
    if max_tokens is not None:
        payload_dict["max_tokens"] = max_tokens
    payload = json.dumps(payload_dict).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - user-configured local endpoint
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"].strip()
    return content, {"latency_ms": int((time.monotonic() - started) * 1000), "raw_response_keys": sorted(body.keys())}


def translate_abstract(text: str, *, translator: Translator | None = None) -> dict[str, Any]:
    clean = (text or "").strip()
    if not clean:
        return {"status": "empty", "text": "", "backend": None, "error": None}
    if translator:
        translated = translator(clean)
        if isinstance(translated, dict):
            return {
                "status": translated.get("status") or "ready",
                "text": translated.get("text") or "",
                "backend": translated.get("backend") or "injected",
                "error": translated.get("error"),
            }
        return {"status": "ready", "text": str(translated), "backend": "injected", "error": None}

    settings = _translation_settings()
    base_url = settings["base_url"]
    if not base_url:
        return {
            "status": "not_configured",
            "text": "",
            "backend": settings["backend"],
            "model": settings["model"],
            "base_url": None,
            "api_key_configured": settings["api_key_configured"],
            "error": "Set TRANSLATION_LLM_BASE_URL or HYMT2_OPENAI_BASE_URL for local abstract translation.",
        }

    try:
        content, meta = _chat_completion(
            base_url=base_url,
            model=settings["model"],
            api_key=settings["api_key"],
            timeout=90,
            messages=[
                {
                    "role": "system",
                    "content": "Translate academic paper abstracts into precise Simplified Chinese. Preserve terminology, numbers, citations, and DOI-like strings. Output only the translation.",
                },
                {"role": "user", "content": clean},
            ],
        )
        return {
            "status": "ready",
            "text": content,
            "backend": settings["backend"],
            "model": settings["model"],
            "base_url": base_url,
            "api_key_configured": settings["api_key_configured"],
            "latency_ms": meta["latency_ms"],
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "text": "",
            "backend": settings["backend"],
            "model": settings["model"],
            "base_url": base_url,
            "api_key_configured": settings["api_key_configured"],
            "error": str(exc),
        }


def _format_display(abstract_en: str, abstract_zh: str, language_mode: str) -> str:
    if language_mode == "en":
        return abstract_en
    if language_mode == "zh":
        return abstract_zh or abstract_en
    if abstract_zh:
        return f"English:\n{abstract_en}\n\n中文：\n{abstract_zh}"
    return abstract_en


def discover_literature(
    *,
    query: str,
    keywords: str | list[str] | tuple[str, ...] | None = None,
    journals: str | list[str] | tuple[str, ...] | None = None,
    year_from: Any = None,
    year_to: Any = None,
    max_results: Any = 10,
    language_mode: str = "bilingual",
    translate: bool = True,
    fetcher: Fetcher | None = None,
    translator: Translator | None = None,
) -> dict[str, Any]:
    mode = language_mode if language_mode in VALID_LANGUAGE_MODES else "bilingual"
    started = time.monotonic()
    results, search_meta = search_openalex(
        query=query,
        keywords=keywords,
        journals=journals,
        year_from=year_from,
        year_to=year_to,
        max_results=max_results,
        fetcher=fetcher,
    )

    translation_statuses: list[str] = []
    for item in results:
        should_translate = translate and mode in {"bilingual", "zh"}
        if should_translate:
            translated = translate_abstract(item.get("abstract_en") or "", translator=translator)
            item["abstract_zh"] = translated.get("text") or ""
            item["translation_status"] = translated.get("status") or "unknown"
            item["translation_error"] = translated.get("error")
            item["translation_backend"] = translated.get("backend")
            if translated.get("model"):
                item["translation_model"] = translated.get("model")
        else:
            item["translation_status"] = "not_requested"
        translation_statuses.append(item["translation_status"])
        item["abstract_display"] = _format_display(item.get("abstract_en") or "", item.get("abstract_zh") or "", mode)

    warnings: list[str] = []
    if translate and mode in {"bilingual", "zh"} and any(status != "ready" for status in translation_statuses):
        warnings.append("Some abstracts could not be translated locally; English abstracts are retained as fallback.")
    if search_meta.get("journal_terms") and search_meta.get("filtered_count") == 0:
        warnings.append("No returned articles matched the journal constraint; try loosening the journal list or year range.")

    return {
        "status": "ready",
        "source": "openalex",
        "query": query,
        "keywords": split_terms(keywords),
        "journals": split_terms(journals),
        "year_from": _year(year_from),
        "year_to": _year(year_to),
        "language_mode": mode,
        "translate": bool(translate),
        "count": len(results),
        "results": results,
        "search": search_meta,
        "translation": {
            "requested": bool(translate and mode in {"bilingual", "zh"}),
            "statuses": sorted(set(translation_statuses)),
            "settings": {key: value for key, value in _translation_settings().items() if key != "api_key"},
        },
        "warnings": warnings,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
