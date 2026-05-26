from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from . import config


def _clip(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _has_private_evidence(evidence: list[dict[str, Any]]) -> bool:
    return any(item.get("sensitivity") in config.PRIVATE_SENSITIVITIES for item in evidence)


def _api_chat(question: str, evidence: list[dict[str, Any]]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured in .env")
    model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    evidence_text = "\n\n".join(
        f"[{idx}] source_id={item['source_id']} file={item['original_filename']} "
        f"page={item.get('page_number')} found_by={','.join(item.get('found_by', []))}\n"
        f"{_clip(item.get('text', ''), 1200)}"
        for idx, item in enumerate(evidence, start=1)
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Answer only from the supplied evidence. If the evidence is insufficient, "
                        "say it was not found in the indexed database. Cite source_id and page."
                    ),
                },
                {"role": "user", "content": f"Question: {question}\n\nEvidence:\n{evidence_text}"},
            ],
            "temperature": 0.1,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as response:  # nosec - configured API endpoint
        body = json.loads(response.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"].strip()


def local_extractive_answer(question: str, evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "没有在已索引数据库中找到足够证据。"
    lines = [
        "基于当前检索到的证据，最相关的信息如下：",
    ]
    for idx, item in enumerate(evidence[:5], start=1):
        page = item.get("page_number")
        page_part = f" p.{page}" if page else ""
        found_by = ", ".join(item.get("found_by", []))
        lines.append(
            f"{idx}. {item['original_filename']}{page_part} "
            f"({item['source_id']}, found_by={found_by}): {_clip(item.get('text', ''), 420)}"
        )
    lines.append("如果这些片段没有覆盖问题的关键点，则应视为当前索引库未找到充分依据。")
    return "\n".join(lines)


def generate_answer(
    question: str,
    evidence: list[dict[str, Any]],
    analysis_model: str,
    *,
    allow_private_api: bool = False,
) -> dict[str, Any]:
    if not evidence:
        return {
            "answer": "没有在已索引数据库中找到相关证据。",
            "analysis_backend": "none",
            "api_used": False,
            "warning": None,
        }

    wants_api = analysis_model == "api_llm"
    auto_api = analysis_model == "auto" and bool(os.getenv("OPENAI_API_KEY")) and not _has_private_evidence(evidence)
    use_api = wants_api or auto_api
    if use_api:
        if _has_private_evidence(evidence) and not allow_private_api:
            return {
                "answer": "检测到私密或机密证据，未调用 API LLM。请使用 Private Local Only 或显式允许私密资料 API 分析。",
                "analysis_backend": "blocked_by_privacy_policy",
                "api_used": False,
                "warning": "Private evidence blocks API analysis by default.",
            }
        try:
            return {
                "answer": _api_chat(question, evidence),
                "analysis_backend": os.getenv("OPENAI_CHAT_MODEL", "openai_chat_model"),
                "api_used": True,
                "warning": None,
            }
        except Exception as exc:
            if wants_api:
                return {
                    "answer": f"API LLM 未运行：{exc}",
                    "analysis_backend": "api_llm_error",
                    "api_used": False,
                    "warning": str(exc),
                }

    return {
        "answer": local_extractive_answer(question, evidence),
        "analysis_backend": "local_extractive_v1",
        "api_used": False,
        "warning": None,
    }
