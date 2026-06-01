from __future__ import annotations

import csv
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from . import config
from .db import connect, init_db, utc_now
from .output_studio import generate_markdown_output
from .text_utils import safe_filename


DEFAULT_GOAL = "基于当前公开论文库得出可执行研究方案"
DEFAULT_SCOPE = "public-papers"
PACKET_OUTPUTS = (
    {
        "output_type": "source_grounded_report",
        "filename": "01_source_grounded_report.md",
        "question": (
            "基于当前文件库所有公开论文，提炼研究方向、核心问题、方法路线、"
            "证据缺口和可执行研究方案。要求区分已由论文支持的结论和需要人工确认的推断。"
        ),
    },
    {
        "output_type": "comparison_matrix",
        "filename": "02_comparison_matrix.md",
        "question": (
            "把当前公开论文按研究问题、方法、数据、实验对象、主要发现、局限和可复用方案做对比矩阵。"
        ),
    },
)


def _clip(text: str, limit: int = 900) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _packet_stamp() -> str:
    return utc_now().replace(":", "-").replace("+00-00", "Z").replace("+00:00", "Z")


def _public_paper_sources() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT s.source_id, s.original_filename, s.domain, s.topic, s.sensitivity,
                   s.source_type, s.ingestion_status, s.ingested_at,
                   COUNT(ch.chunk_id) AS chunk_count
            FROM sources s
            LEFT JOIN chunks ch ON ch.source_id=s.source_id
            WHERE s.domain='paper' AND s.sensitivity='public'
            GROUP BY s.source_id
            ORDER BY s.original_filename
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _write_manifest_csv(path: Path, sources: list[dict[str, Any]]) -> None:
    fieldnames = [
        "source_id",
        "original_filename",
        "topic",
        "sensitivity",
        "source_type",
        "ingestion_status",
        "chunk_count",
        "ingested_at",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for source in sources:
            writer.writerow({key: source.get(key) for key in fieldnames})


def _source_sample_chunks(source_id: str, limit: int = 2) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT chunk_id, page_number, section_title, text
            FROM chunks
            WHERE source_id=?
            ORDER BY chunk_index
            LIMIT ?
            """,
            (source_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _write_corpus_snapshot(path: Path, sources: list[dict[str, Any]]) -> None:
    lines = [
        "# Corpus Snapshot",
        "",
        "This file is a compact local snapshot for ChatGPT. It lists every public paper source in scope and a small sample of canonical chunks. It does not include original PDF files.",
        "",
        f"- Scope: `{DEFAULT_SCOPE}`",
        f"- Public paper sources: {len(sources)}",
        "",
    ]
    for idx, source in enumerate(sources, start=1):
        lines.extend(
            [
                f"## {idx}. {source['original_filename']}",
                "",
                f"- source_id: `{source['source_id']}`",
                f"- topic: {source.get('topic') or ''}",
                f"- chunk_count: {source.get('chunk_count') or 0}",
                f"- ingestion_status: {source.get('ingestion_status') or ''}",
                "",
            ]
        )
        samples = _source_sample_chunks(source["source_id"])
        if not samples:
            lines.extend(["- Sample chunks: none indexed", ""])
            continue
        for sample in samples:
            page = sample.get("page_number") or "unknown"
            section = sample.get("section_title") or "unknown section"
            lines.extend(
                [
                    f"### Sample chunk `{sample['chunk_id']}`",
                    f"- page: {page}",
                    f"- section: {section}",
                    "",
                    _clip(sample.get("text", ""), 1000),
                    "",
                ]
            )
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _build_prompt(*, goal: str, source_count: int, retrieval_mode: str, top_k: int) -> str:
    return f"""你是 GPT-5.5 Pro。请基于我上传的本地论文库资料，帮我形成一个可执行研究方案。

我的目标：
{goal}

资料包说明：
- 这个 zip 是本地 Personal Research OS 生成的 ChatGPT research packet。
- `paper_manifest.csv` 是公开论文清单，共 {source_count} 篇。
- `00_corpus_snapshot.md` 是每篇公开论文的紧凑索引快照。
- `01_source_grounded_report.md` 和 `02_comparison_matrix.md` 是本地检索系统生成的证据导向 Markdown。
- 本地检索模式：`{retrieval_mode}`，每个输出的返回数量：`{top_k}`。
- 原始 PDF 不在 zip 中。我会在 ChatGPT 项目里另行上传或复制关键论文原文。

工作要求：
1. 先读 `paper_manifest.csv`，确认全库论文范围，不要只分析被检索命中的片段。
2. 再读 `00_corpus_snapshot.md`、`01_source_grounded_report.md`、`02_comparison_matrix.md`。
3. 如果我另外上传了原始 PDF，必要时回查 PDF 原文，不要只依赖摘要或快照。
4. 所有关键结论都要标注来自哪个文件、source_id、页面或 evidence 编号。
5. 无法从上传资料证明的内容，必须标记为“推断/待验证”。
6. 不要把本地文件路径当作证据；以论文文件名、source_id、页面和 evidence 为准。

请输出：
- 研究方向地图
- 现有论文共识
- 争议点和证据缺口
- 3-5 个可做的研究问题
- 每个研究问题的可行性、创新性、数据需求、风险
- 推荐主线研究方案
- 4 周 / 8 周执行计划
- 还需要补充的论文、实验或验证
"""


def _copy_to_clipboard(text: str, *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "copied": False, "error": None}
    pbcopy = shutil.which("pbcopy")
    if not pbcopy:
        return {"status": "unavailable", "copied": False, "error": "pbcopy is not available"}
    try:
        subprocess.run([pbcopy], input=text, text=True, check=True)
    except Exception as exc:
        return {"status": "failed", "copied": False, "error": str(exc)}
    return {"status": "copied", "copied": True, "error": None}


def _zip_packet(packet_dir: Path) -> Path:
    zip_path = packet_dir.parent / f"{packet_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(packet_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(packet_dir.parent))
    return zip_path


def generate_chatgpt_packet(
    *,
    goal: str = DEFAULT_GOAL,
    retrieval_mode: str = "strict_exhaustive",
    top_k: int = 40,
    output_dir: Path | None = None,
    copy_prompt: bool = True,
) -> dict[str, Any]:
    init_db()
    sources = _public_paper_sources()
    if not sources:
        return {
            "status": "blocked",
            "reason": "No public paper sources found. Ingest public papers before generating a ChatGPT packet.",
            "scope": DEFAULT_SCOPE,
            "paper_count": 0,
        }

    stamp = _packet_stamp()
    base_dir = output_dir or (config.OUTPUT_DIR / "chatgpt_packets")
    packet_dir = base_dir / f"{stamp}_{safe_filename(goal)[:64]}"
    packet_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = packet_dir / "paper_manifest.csv"
    snapshot_path = packet_dir / "00_corpus_snapshot.md"
    prompt_path = packet_dir / "chatgpt_prompt.md"
    readme_path = packet_dir / "README_UPLOAD_TO_CHATGPT.md"
    metadata_path = packet_dir / "packet_metadata.json"

    _write_manifest_csv(manifest_path, sources)
    _write_corpus_snapshot(snapshot_path, sources)

    source_ids = [source["source_id"] for source in sources]
    generated_outputs = []
    for spec in PACKET_OUTPUTS:
        result = generate_markdown_output(
            output_type=spec["output_type"],
            question=f"{goal}\n\n{spec['question']}",
            title=spec["output_type"],
            retrieval_mode=retrieval_mode,
            filters={"domains": ["paper"], "sensitivities": ["public"], "source_ids": source_ids},
            top_k=top_k,
            llm_backend="none",
        )
        target_path = packet_dir / spec["filename"]
        target_path.write_text(result["content"], encoding="utf-8")
        generated_outputs.append(
            {
                "output_type": spec["output_type"],
                "packet_file": target_path.name,
                "source_file": result["file_path"],
                "quality_checks": result["quality_checks"],
                "retrieval": result["retrieval"],
            }
        )

    prompt = _build_prompt(goal=goal, source_count=len(sources), retrieval_mode=retrieval_mode, top_k=top_k)
    prompt_path.write_text(prompt, encoding="utf-8")
    readme_path.write_text(
        "\n".join(
            [
                "# Upload This Packet To ChatGPT",
                "",
                "1. Upload the zip file in a ChatGPT Project or a GPT-5.5 Pro chat.",
                "2. Paste the clipboard prompt if it is not already in the composer.",
                "3. Upload or paste original PDF files separately when deeper source checking is needed.",
                "4. Do not upload private or confidential sources unless you explicitly accept that cloud boundary.",
                "",
                "This packet intentionally excludes original PDF files.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    metadata = {
        "status": "ready",
        "created_at": utc_now(),
        "goal": goal,
        "scope": DEFAULT_SCOPE,
        "paper_count": len(sources),
        "retrieval_mode": retrieval_mode,
        "top_k": top_k,
        "included_files": [
            manifest_path.name,
            snapshot_path.name,
            *[item["packet_file"] for item in generated_outputs],
            prompt_path.name,
            readme_path.name,
            metadata_path.name,
        ],
        "raw_pdfs_included": False,
        "generated_outputs": generated_outputs,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = _zip_packet(packet_dir)
    clipboard = _copy_to_clipboard(prompt, enabled=copy_prompt)

    return {
        **metadata,
        "packet_dir": str(packet_dir),
        "zip_path": str(zip_path),
        "prompt_path": str(prompt_path),
        "metadata_path": str(metadata_path),
        "clipboard": clipboard,
    }
