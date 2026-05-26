from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import config
from .db import connect, init_db, json_dumps, utc_now
from .retrieval import retrieve
from .text_utils import sha256_text

OUTPUT_TYPES = {
    "research_summary": "研究摘要",
    "literature_review": "文献综述",
    "comparison_matrix": "对比矩阵",
    "source_grounded_report": "来源扎根报告",
    "mindmap_outline": "思维导图大纲",
    "presentation_guidance": "汇报指导包",
    "presentation_codex_execution_guidance": "汇报 Codex 执行指导",
    "presentation_codex_prompt": "汇报 Codex Prompt",
    "project_repair_guidance": "项目修复指导",
    "project_next_step_guidance": "项目下一步指导",
}


def _clip(text: str, limit: int = 900) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _citation_line(item: dict[str, Any], idx: int) -> str:
    page = f"p.{item.get('page_number')}" if item.get("page_number") else "page unknown"
    found_by = ", ".join(item.get("found_by", []))
    return f"[{idx}] {item['original_filename']} / {item['source_id']} / {page} / found_by={found_by}"


def deterministic_scaffold(
    *,
    output_type: str,
    title: str,
    question: str,
    evidence: list[dict[str, Any]],
    retrieval_meta: dict[str, Any],
) -> str:
    generated_at = utc_now()
    citations = [_citation_line(item, idx) for idx, item in enumerate(evidence, start=1)]
    evidence_block = "\n\n".join(
        f"### Evidence {idx}\n- Citation: {_citation_line(item, idx)}\n- Snippet: {_clip(item.get('text', ''), 1200)}"
        for idx, item in enumerate(evidence, start=1)
    )
    unsupported = "- [无直接来源，需人工确认] 需要补充人工判断或追加资料。" if not evidence else ""
    return f"""# {title}

## 1. 生成信息
- Output type: `{output_type}`
- Generation time: {generated_at}
- Question / task: {question or "未提供"}
- Retrieval mode: {retrieval_meta.get("retrieval_mode")}
- Evidence count: {len(evidence)}

## 2. Source Scope
- Included sources:
{chr(10).join(f"  - {line}" for line in citations) if citations else "  - 未检索到来源"}
- Source coverage note: 当前输出只基于已导入、已索引、可检索的资料。

## 3. Citation Policy
- 所有实质性结论必须能回指到 Evidence 编号。
- 没有直接来源的推断必须标记 `[无直接来源，需人工确认]`。
- 不把重复 chunk 重复计入证据。

## 4. Draft Content
{_draft_body(output_type, evidence)}
{unsupported}

## 5. Evidence / Citation Section
{evidence_block if evidence_block else "没有检索到可用 evidence。"}

## 6. Uncertainty / Missing Information
- API vector missing: {"yes" if "api_vector" in retrieval_meta.get("errors", []) else "see audit warnings"}
- Retrieval warnings: {", ".join(retrieval_meta.get("errors", [])) or "none"}
- Missing information note: 如需完整结论，请补充未导入论文、图表 OCR 或失败索引。

## 7. Next Actions
- 检查引用是否覆盖核心论点。
- 对 `[无直接来源，需人工确认]` 的句子补充来源或删除。
- 必要时在 Maintenance 里重建缺失索引。

## 8. Quality Checklist
- [ ] 每个关键论点有 citation
- [ ] 没有 unsupported claim
- [ ] source coverage note 已阅读
- [ ] duplicate evidence 已合并
- [ ] missing information 已标明

## 9. Export Metadata
```json
{json.dumps({"output_type": output_type, "generated_at": generated_at, "evidence_chunk_ids": [item["chunk_id"] for item in evidence]}, ensure_ascii=False, indent=2)}
```
"""


def _draft_body(output_type: str, evidence: list[dict[str, Any]]) -> str:
    if output_type == "comparison_matrix":
        rows = ["| Topic | Evidence | Notes |", "|---|---|---|"]
        for idx, item in enumerate(evidence[:8], start=1):
            rows.append(f"| {item['original_filename']} | [{idx}] | {_clip(item.get('text', ''), 180)} |")
        return "\n".join(rows)
    if output_type == "mindmap_outline":
        lines = ["- 中心主题"]
        for idx, item in enumerate(evidence[:8], start=1):
            lines.append(f"  - 证据 {idx}: {item['original_filename']}")
            lines.append(f"    - {_clip(item.get('text', ''), 160)}")
        return "\n".join(lines)
    if output_type.startswith("presentation"):
        return """## Slide-by-Slide Structure
### Slide 1 — Title
- Purpose: 明确主题和研究问题
- Main content: [无直接来源，需人工确认]
- Visual suggestion: 使用研究对象或核心流程图
- Speaker notes: 引出 source coverage
- Source citations: 见 Evidence

### Slide 2 — Evidence Map
- Purpose: 展示资料范围与证据来源
- Main content: 基于检索 evidence 建立来源地图
- Visual suggestion: source × theme 矩阵
- Speaker notes: 说明缺失资料和检索边界
- Source citations: 见 Evidence
"""
    return "\n".join(
        f"{idx}. 基于 [{idx}]，{_clip(item.get('text', ''), 260)}"
        for idx, item in enumerate(evidence[:8], start=1)
    ) or "[无直接来源，需人工确认] 当前没有足够 evidence 生成正文。"


def _local_llm_settings() -> dict[str, Any]:
    base_url = os.getenv("GEMMA4_OPENAI_BASE_URL") or os.getenv("LOCAL_LLM_BASE_URL")
    model = os.getenv("GEMMA4_MODEL") or os.getenv("LOCAL_LLM_MODEL") or "gemma4"
    api_key = os.getenv("GEMMA4_OPENAI_API_KEY") or os.getenv("LOCAL_LLM_API_KEY")
    return {
        "backend": "gemma4",
        "base_url": base_url,
        "model": model,
        "api_key_configured": bool(api_key),
        "api_key": api_key,
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
    payload_dict: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
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


def check_local_llm(timeout: int = 8) -> dict[str, Any]:
    settings = _local_llm_settings()
    base_url = settings["base_url"]
    model = settings["model"]
    if not base_url:
        return {
            "backend": "gemma4",
            "configured": False,
            "reachable": False,
            "status": "not_configured",
            "model": model,
            "base_url": None,
            "api_key_configured": settings["api_key_configured"],
            "error": "Set LOCAL_LLM_BASE_URL or GEMMA4_OPENAI_BASE_URL in .env.",
        }
    try:
        content, meta = _chat_completion(
            base_url=base_url,
            model=model,
            api_key=settings["api_key"],
            timeout=timeout,
            max_tokens=8,
            messages=[
                {"role": "system", "content": "Reply with OK."},
                {"role": "user", "content": "diagnostic ping"},
            ],
        )
        return {
            "backend": "gemma4",
            "configured": True,
            "reachable": True,
            "status": "ready",
            "model": model,
            "base_url": base_url,
            "api_key_configured": settings["api_key_configured"],
            "latency_ms": meta["latency_ms"],
            "sample": content[:80],
            "error": None,
        }
    except Exception as exc:
        return {
            "backend": "gemma4",
            "configured": True,
            "reachable": False,
            "status": "failed",
            "model": model,
            "base_url": base_url,
            "api_key_configured": settings["api_key_configured"],
            "error": str(exc),
        }


def _call_gemma4(prompt: str, evidence: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any]]:
    settings = _local_llm_settings()
    base_url = settings["base_url"]
    model = settings["model"]
    if not base_url:
        return None, {"backend": "gemma4", "model": model, "status": "fallback", "error": "LOCAL_LLM_BASE_URL is not configured"}
    started = time.monotonic()
    try:
        content, meta = _chat_completion(
            base_url=base_url,
            model=model,
            api_key=settings["api_key"],
            messages=[
                {
                    "role": "system",
                    "content": "只根据用户提供的 evidence 写 Markdown。没有来源的内容必须标记 [无直接来源，需人工确认]。",
                },
                {"role": "user", "content": prompt},
            ],
            timeout=90,
        )
        return content, {
            "backend": "gemma4",
            "model": model,
            "status": "ready",
            "latency_ms": meta["latency_ms"],
            "error": None,
        }
    except Exception as exc:
        return None, {
            "backend": "gemma4",
            "model": model,
            "status": "fallback",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": str(exc),
        }


def _pack_scaffold(
    *,
    output_type: str,
    title: str,
    question: str,
    template_markdown: str,
    evidence: list[dict[str, Any]],
    retrieval_meta: dict[str, Any],
) -> str:
    generated_at = utc_now()
    evidence_rows = ["| # | Source | Page | Found By | Evidence |", "|---|---|---|---|---|"]
    for idx, item in enumerate(evidence, start=1):
        page = item.get("page_number") or "unknown"
        found_by = ", ".join(item.get("found_by", []))
        evidence_rows.append(
            f"| {idx} | {item['original_filename']} | {page} | {found_by} | {_clip(item.get('text', ''), 260)} |"
        )
    if len(evidence_rows) == 2:
        evidence_rows.append("| - | No retrieved source | - | - | Add project sources or rebuild indexes. |")
    return f"""# {title}

## 1. Generation Context
- Output type: `{output_type}`
- Generation time: {generated_at}
- Question / task: {question or "未提供"}
- Retrieval mode: {retrieval_meta.get("retrieval_mode")}
- Evidence count: {len(evidence)}

## 2. Pack Template
{template_markdown.strip()}

## 3. Source Scope / Evidence Table
{chr(10).join(evidence_rows)}

## 4. Unsupported Claims
- Any statement not tied to the evidence table must stay marked as `[无直接来源，需人工确认]`.

## 5. Next Verification
- Check whether the project source set is complete.
- Accept, reject, or revise any unsupported claim before sharing this output.

## 6. Export Metadata
```json
{json.dumps({"output_type": output_type, "generated_at": generated_at, "evidence_chunk_ids": [item["chunk_id"] for item in evidence]}, ensure_ascii=False, indent=2)}
```
"""


def _persist_markdown_output(
    *,
    output_type: str,
    title: str,
    question: str,
    content: str,
    prompt: str,
    evidence: list[dict[str, Any]],
    retrieval: dict[str, Any],
    llm_meta: dict[str, Any],
) -> dict[str, Any]:
    output_id = "out_" + uuid.uuid4().hex
    output_dir = config.OUTPUT_DIR / output_type
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{output_id}.md"
    file_path.write_text(content, encoding="utf-8")
    citations = [
        {
            "chunk_id": item["chunk_id"],
            "source_id": item["source_id"],
            "source_file": item["original_filename"],
            "page_number": item.get("page_number"),
            "found_by": item.get("found_by", []),
        }
        for item in evidence
    ]
    quality = {
        "citation_coverage": "ok" if evidence else "missing",
        "unsupported_claim_marker_required": True,
        "duplicate_evidence_count": len(retrieval.get("dropped_duplicates", [])),
        "human_review_required": llm_meta.get("status") != "ready",
        "warnings": retrieval.get("errors", []),
    }
    with connect() as con:
        con.execute(
            """
            INSERT INTO local_llm_runs (
              run_id, backend, model_name, prompt_hash, evidence_chunk_ids_json,
              status, error, created_at, latency_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "llm_" + uuid.uuid4().hex,
                llm_meta["backend"],
                llm_meta["model"],
                sha256_text(prompt),
                json_dumps([item["chunk_id"] for item in evidence]),
                llm_meta["status"],
                llm_meta.get("error"),
                utc_now(),
                llm_meta.get("latency_ms"),
            ),
        )
        con.execute(
            """
            INSERT INTO markdown_outputs (
              output_id, output_type, title, question, selected_sources_json,
              content, citations_json, quality_checks_json, llm_backend, created_at, file_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                output_id,
                output_type,
                title,
                question,
                json_dumps(sorted({item["source_id"] for item in evidence})),
                content,
                json_dumps(citations),
                json_dumps(quality),
                llm_meta["backend"],
                utc_now(),
                str(file_path),
            ),
        )
    return {
        "output_id": output_id,
        "output_type": output_type,
        "title": title,
        "content": content,
        "file_path": str(file_path),
        "citations": citations,
        "quality_checks": quality,
        "llm": llm_meta,
        "retrieval": {
            "errors": retrieval.get("errors", []),
            "raw_result_counts": retrieval.get("raw_result_counts", {}),
            "merged_count": retrieval.get("merged_count"),
        },
    }


def generate_markdown_output(
    *,
    output_type: str,
    question: str,
    title: str | None = None,
    retrieval_mode: str = "all_available",
    filters: dict[str, Any] | None = None,
    top_k: int = 10,
    llm_backend: str = "gemma4",
) -> dict[str, Any]:
    init_db()
    if output_type not in OUTPUT_TYPES:
        raise ValueError(f"Unsupported output_type: {output_type}")
    title = title or OUTPUT_TYPES[output_type]
    retrieval = retrieve(question or title, retrieval_mode=retrieval_mode, filters=filters or {}, top_k=top_k)
    evidence = retrieval["evidence"]
    scaffold = deterministic_scaffold(
        output_type=output_type,
        title=title,
        question=question,
        evidence=evidence,
        retrieval_meta=retrieval,
    )
    prompt = scaffold
    llm_content = None
    llm_meta = {"backend": llm_backend, "model": llm_backend, "status": "skipped", "error": None}
    if llm_backend == "gemma4":
        llm_content, llm_meta = _call_gemma4(prompt, evidence)
    content = llm_content or scaffold
    return _persist_markdown_output(
        output_type=output_type,
        title=title,
        question=question,
        content=content,
        prompt=prompt,
        evidence=evidence,
        retrieval=retrieval,
        llm_meta=llm_meta,
    )


def list_available_output_types(project_id: str | None = None) -> list[dict[str, str]]:
    output_types = [
        {"output_type": output_type, "title": title, "source": "core"}
        for output_type, title in OUTPUT_TYPES.items()
    ]
    if not project_id:
        return output_types
    from .research_packs import list_pack_templates
    from .research_projects import get_project

    project = get_project(project_id)
    if not project or not project.get("pack_id"):
        return output_types
    try:
        templates = list_pack_templates(project["pack_id"])
    except Exception:
        return output_types
    output_types.extend(
        {
            "output_type": template["output_type"],
            "title": template["title"],
            "source": "pack",
        }
        for template in templates.values()
    )
    return output_types


def generate_project_markdown_output(
    *,
    project_id: str,
    output_type: str,
    question: str,
    title: str | None = None,
    retrieval_mode: str = "all_available",
    top_k: int = 10,
    llm_backend: str = "gemma4",
) -> dict[str, Any]:
    init_db()
    from .research_packs import list_pack_templates, load_pack_template
    from .research_projects import get_project, retrieve_for_project

    project = get_project(project_id)
    if not project:
        raise ValueError("Project not found")
    pack_templates = {}
    if project.get("pack_id"):
        try:
            pack_templates = list_pack_templates(project["pack_id"])
        except Exception:
            if output_type not in OUTPUT_TYPES:
                raise
    is_core_output = output_type in OUTPUT_TYPES
    is_pack_output = output_type in pack_templates
    if not is_core_output and not is_pack_output:
        raise ValueError(f"Unsupported output_type for project: {output_type}")

    title = title or (OUTPUT_TYPES.get(output_type) if is_core_output else pack_templates[output_type]["title"])
    retrieval = retrieve_for_project(
        project_id,
        question or title,
        retrieval_mode=retrieval_mode,
        top_k=top_k,
    )
    evidence = retrieval["evidence"]
    if is_core_output:
        scaffold = deterministic_scaffold(
            output_type=output_type,
            title=title,
            question=question,
            evidence=evidence,
            retrieval_meta=retrieval,
        )
    else:
        scaffold = _pack_scaffold(
            output_type=output_type,
            title=title,
            question=question,
            template_markdown=load_pack_template(project["pack_id"], output_type),
            evidence=evidence,
            retrieval_meta=retrieval,
        )
    prompt = scaffold
    llm_content = None
    llm_meta = {"backend": llm_backend, "model": llm_backend, "status": "skipped", "error": None}
    if llm_backend == "gemma4":
        llm_content, llm_meta = _call_gemma4(prompt, evidence)
    result = _persist_markdown_output(
        output_type=output_type,
        title=title,
        question=question,
        content=llm_content or scaffold,
        prompt=prompt,
        evidence=evidence,
        retrieval=retrieval,
        llm_meta=llm_meta,
    )
    result["project_id"] = project_id
    result["pack_id"] = project.get("pack_id")
    return result


def list_markdown_outputs(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as con:
        rows = con.execute(
            """
            SELECT output_id, output_type, title, question, file_path, llm_backend, created_at,
                   quality_checks_json
            FROM markdown_outputs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
