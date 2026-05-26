from __future__ import annotations

import argparse
from pathlib import Path


SAMPLES = [
    (
        "Maximum Recall Retrieval for Multimodal Papers",
        "Maximum recall retrieval combines local vector search, API vector search, BM25 keyword search, "
        "and entity graph traversal. Results are merged by canonical chunk_id and source_id.",
    ),
    (
        "Canonical Structured Data for Personal Knowledge Bases",
        "Canonical chunks store source_id, chunk_id, page number, sensitivity, parser version, content hash, "
        "and multimodal placeholders for figures, tables, equations, captions, and OCR.",
    ),
    (
        "Privacy Defaults for Personal Archives",
        "Private screenshots, WeChat chat records, banking documents, account materials, and private notes "
        "default to Private Local Only retrieval and local analysis.",
    ),
    (
        "Index Coverage Maintenance",
        "Maintenance checks local vector, API vector, BM25, and graph coverage for every chunk. Missing, stale, "
        "and failed indexes can be rebuilt from canonical data.",
    ),
    (
        "Grounded Answers With Source Trace",
        "Answers cite source_id, filename, page number or timestamp, and found_by provenance. Duplicate evidence "
        "is removed before analysis.",
    ),
]


def make_pdf(path: Path, title: str, body: str) -> None:
    import fitz  # type: ignore

    doc = fitz.open()
    text = title + "\n\n" + ((body + "\n\n") * 10)
    for start in range(0, len(text), 2600):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(
            fitz.Rect(72, 72, 540, 720),
            text[start : start + 2600],
            fontsize=10,
            fontname="helv",
            align=0,
        )
    doc.save(path)
    doc.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/pkb-sample-papers")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for idx, (title, body) in enumerate(SAMPLES, start=1):
        make_pdf(out / f"sample_paper_{idx}.pdf", title, body)
    print(out)


if __name__ == "__main__":
    main()
