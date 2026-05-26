from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="")
    args = parser.parse_args()
    data_root = Path(args.data_root) if args.data_root else Path(tempfile.mkdtemp(prefix="pkb-smoke-"))
    os.environ["PKB_DATA_DIR"] = str(data_root / "data")
    os.environ["PKB_DB_DIR"] = str(data_root / "db")
    os.environ["PKB_INDEX_DIR"] = str(data_root / "indexes")
    os.environ["PKB_CACHE_DIR"] = str(data_root / "cache")
    os.environ["PKB_BACKUP_DIR"] = str(data_root / "backups")
    os.environ["PKB_OUTPUT_DIR"] = str(data_root / "outputs")
    os.environ["LOCAL_MODELS_DIR"] = str(data_root / "local_models")

    sample_dir = data_root / "papers"
    subprocess.run([sys.executable, "scripts/make_sample_papers.py", "--out", str(sample_dir)], check=True)

    from app.db import connect, coverage_summary, init_db
    from app.ingest import ingest_folder
    from app.retrieval import answer_query

    init_db()
    ingest = ingest_folder(sample_dir, domain="paper", topic="smoke", sensitivity="public")
    result = answer_query(
        "How are all available indexes merged without duplicate evidence?",
        retrieval_mode="all_available",
        analysis_model="local_llm",
        filters={"domains": ["paper"], "sensitivities": ["public"]},
        top_k=6,
    )
    with connect() as con:
        coverage = coverage_summary(con)
    print({"data_root": str(data_root), "ingest": ingest["message"], "coverage": coverage})
    print(result["analysis"]["answer"])
    print([{"chunk_id": e["chunk_id"], "found_by": e["found_by"]} for e in result["retrieval"]["evidence"]])


if __name__ == "__main__":
    main()
