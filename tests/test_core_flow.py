from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def make_pdf(path: Path, title: str, body: str) -> None:
    import fitz  # type: ignore

    doc = fitz.open()
    text = title + "\n\n" + body
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


def restore_env(keys: list[str], old_values: dict[str, str | None]) -> None:
    for key in keys:
        if old_values[key] is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_values[key] or ""


class CoreFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        os.environ["PKB_DATA_DIR"] = str(root / "data")
        os.environ["PKB_DB_DIR"] = str(root / "db")
        os.environ["PKB_INDEX_DIR"] = str(root / "indexes")
        os.environ["PKB_CACHE_DIR"] = str(root / "cache")
        os.environ["PKB_BACKUP_DIR"] = str(root / "backups")
        os.environ["PKB_OUTPUT_DIR"] = str(root / "outputs")
        os.environ["LOCAL_MODELS_DIR"] = str(root / "local_models")
        cls.paper_dir = root / "papers"
        cls.paper_dir.mkdir()
        texts = [
            (
                "Maximum Recall Retrieval for Multimodal Papers",
                "Abstract Maximum recall retrieval combines local vector search, API vector search, BM25 keyword search, "
                "and entity graph traversal. The system merges evidence by canonical chunk_id and records found_by provenance. "
                "Figure 1: Coverage-aware retrieval pipeline for public academic papers.",
            ),
            (
                "Canonical Structured Data for Personal Knowledge Bases",
                "Introduction A canonical structured content layer stores source_id, chunk_id, page number, sensitivity, "
                "parser version, and content hashes. Local and API indexes are separate derived layers.",
            ),
            (
                "Private Local Only Policy for Personal Archives",
                "Methods Private screenshots, WeChat chat records, banking documents, and confidential notes must not be sent "
                "to API retrieval or API analysis by default.",
            ),
            (
                "Index Coverage Maintenance in Long-Term RAG Systems",
                "Results Each chunk needs coverage records for local vector, API vector, BM25, and graph indexes. "
                "Maintenance detects missing indexes, stale indexes, and failed chunks.",
            ),
            (
                "Evidence Grounding and Source Traceability",
                "Conclusion Answers should cite source_id, filename, page number, timestamps when available, and found_by. "
                "Duplicate evidence should not be passed to the language model.",
            ),
        ]
        cls.pdfs = []
        for idx, (title, body) in enumerate(texts, start=1):
            path = cls.paper_dir / f"paper_{idx}.pdf"
            make_pdf(path, title, body * 4)
            cls.pdfs.append(path)

        from app.db import init_db

        init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_pdf_ingestion_indexing_retrieval_and_privacy(self) -> None:
        from app import config
        from app.db import connect, coverage_summary
        from app.ingest import ingest_file
        from app.indexes import rebuild_indexes
        from app.maintenance import maintenance_report
        from app.output_studio import generate_markdown_output
        from app.retrieval import answer_query, retrieve

        results = [ingest_file(path, domain="paper", topic="rag", sensitivity="public") for path in self.pdfs]
        self.assertTrue(all(result.status == "ready" for result in results))
        self.assertEqual(len({result.source_id for result in results}), 5)
        self.assertTrue(all(Path(result.raw_path).exists() for result in results if result.raw_path))

        duplicate = ingest_file(self.pdfs[0], domain="paper", topic="rag", sensitivity="public")
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.status, "duplicate")

        rebuild = rebuild_indexes(index_names=[config.LOCAL_VECTOR_INDEX, config.BM25_INDEX, config.GRAPH_INDEX])
        self.assertEqual(rebuild["status"], "ready")

        with connect() as con:
            counts = coverage_summary(con)
            self.assertEqual(counts["total_sources"], 5)
            self.assertGreater(counts["total_chunks"], 0)
            self.assertEqual(counts["indexed_by"][config.LOCAL_VECTOR_INDEX], counts["total_chunks"])
            self.assertEqual(counts["indexed_by"][config.BM25_INDEX], counts["total_chunks"])
            self.assertEqual(counts["indexed_by"][config.GRAPH_INDEX], counts["total_chunks"])
            self.assertEqual(counts["missing_by"][config.API_VECTOR_INDEX], counts["total_chunks"])

        result = answer_query(
            "How does maximum recall retrieval merge local vector API vector BM25 graph evidence?",
            retrieval_mode="all_available",
            analysis_model="local_llm",
            filters={"domains": ["paper"], "sensitivities": ["public"]},
            top_k=6,
        )
        evidence = result["retrieval"]["evidence"]
        self.assertTrue(result["audit_id"].startswith("aud_"))
        self.assertGreater(len(evidence), 0)
        self.assertEqual(len({item["chunk_id"] for item in evidence}), len(evidence))
        self.assertTrue(any(len(item["found_by"]) >= 2 for item in evidence))
        self.assertIn("source_id", evidence[0])
        self.assertIn("page_number", evidence[0])
        self.assertIn("citation_id", evidence[0])

        with connect() as con:
            self.assertGreater(con.execute("SELECT COUNT(*) AS n FROM retrieval_audits").fetchone()["n"], 0)
            self.assertGreater(con.execute("SELECT COUNT(*) AS n FROM retrieval_results").fetchone()["n"], 0)
            self.assertGreater(con.execute("SELECT COUNT(*) AS n FROM citations").fetchone()["n"], 0)

        output = generate_markdown_output(
            output_type="presentation_guidance",
            question="Create a presentation guidance package about retrieval audit and citations.",
            retrieval_mode="all_available",
            filters={"domains": ["paper"], "sensitivities": ["public"]},
            top_k=5,
            llm_backend="gemma4",
        )
        self.assertTrue(Path(output["file_path"]).exists())
        self.assertIn("Citation Policy", output["content"])
        self.assertIn("human_review_required", output["quality_checks"])

        report = maintenance_report()
        self.assertIn("coverage", report)
        self.assertIn("storage", report)
        self.assertGreaterEqual(report["database"]["documents"], 5)

        private_pdf = self.paper_dir / "private_policy.pdf"
        make_pdf(
            private_pdf,
            "Private WeChat Banking Screenshot Policy",
            "Private Local Only applies to WeChat chat records, banking screenshots, account documents, and personal notes.",
        )
        private_result = ingest_file(private_pdf, domain="doc", topic="privacy", sensitivity="private")
        self.assertEqual(private_result.status, "ready")
        private_query = retrieve(
            "What private materials are blocked from API?",
            retrieval_mode="all_available",
            filters={"sensitivities": ["private"]},
            top_k=5,
            allow_private_api=False,
        )
        self.assertIn("API retrieval skipped by privacy policy", private_query["errors"])
        self.assertFalse(private_query["api_retrieval_allowed"])

    def test_merge_deduplicates_local_and_api_chunk_hits(self) -> None:
        from app.retrieval import _merge_groups

        merged = _merge_groups(
            [
                ("local_vector", [{"chunk_id": "c1", "source_id": "s1", "rank": 2, "score": 0.8}]),
                ("api_vector", [{"chunk_id": "c1", "source_id": "s1", "rank": 1, "score": 0.9}]),
                ("bm25", [{"chunk_id": "c2", "source_id": "s1", "rank": 1, "score": 3.0}]),
            ]
        )
        c1 = next(item for item in merged if item["chunk_id"] == "c1")
        self.assertEqual(c1["found_by"], ["local_vector", "api_vector"])
        self.assertEqual(c1["ranks"]["api_vector"], 1)
        self.assertEqual(len([item for item in merged if item["chunk_id"] == "c1"]), 1)

    def test_pdfjs_viewer_assets_and_routes_are_wired(self) -> None:
        root = Path(__file__).resolve().parents[1]
        reader = (root / "static" / "pdf_reader.html").read_text(encoding="utf-8")
        server = (root / "app" / "server.py").read_text(encoding="utf-8")
        self.assertIn("/vendor/pdfjs/legacy/build/pdf.mjs", reader)
        self.assertIn("/vendor/pdfjs/legacy/build/pdf.worker.mjs", reader)
        self.assertIn("/api/source/raw?source_id=", reader)
        self.assertIn("chunk_id", reader)
        self.assertIn("cMapUrl", reader)
        self.assertTrue((root / "vendor" / "pdfjs" / "LICENSE").exists())
        self.assertIn('path.startswith("/vendor/")', server)
        self.assertIn('".mjs": "application/javascript; charset=utf-8"', server)

    def test_local_llm_diagnostic_openai_compatible_endpoint(self) -> None:
        keys = ["LOCAL_LLM_BASE_URL", "GEMMA4_OPENAI_BASE_URL", "LOCAL_LLM_MODEL", "GEMMA4_MODEL"]
        old_values = {key: os.environ.get(key) for key in keys}
        try:
            os.environ.pop("GEMMA4_OPENAI_BASE_URL", None)
            os.environ.pop("GEMMA4_MODEL", None)
            os.environ["LOCAL_LLM_BASE_URL"] = "http://127.0.0.1:9999/v1"
            os.environ["LOCAL_LLM_MODEL"] = "gemma4-mock"
            from app.output_studio import check_local_llm

            with patch(
                "app.output_studio._chat_completion",
                return_value=("mock gemma4 ready", {"latency_ms": 3, "raw_response_keys": ["choices"]}),
            ) as mocked:
                status = check_local_llm(timeout=2)
            self.assertEqual(status["status"], "ready")
            self.assertTrue(status["reachable"])
            self.assertEqual(status["model"], "gemma4-mock")
            self.assertIn("mock gemma4", status["sample"])
            self.assertEqual(mocked.call_args.kwargs["model"], "gemma4-mock")
        finally:
            restore_env(keys, old_values)

    def test_retrieval_audit_detail_and_repair_guidance(self) -> None:
        from app import config
        from app.indexes import rebuild_indexes
        from app.ingest import ingest_file
        from app.maintenance import generate_codex_repair_from_audit, retrieval_audit_detail
        from app.retrieval import answer_query

        for path in self.pdfs[:2]:
            ingest_file(path, domain="paper", topic="audit", sensitivity="public")
        rebuild_indexes(index_names=[config.LOCAL_VECTOR_INDEX, config.BM25_INDEX, config.GRAPH_INDEX])
        result = answer_query(
            "How is coverage-aware retrieval audited?",
            retrieval_mode="all_available",
            analysis_model="local_llm",
            filters={"domains": ["paper"], "sensitivities": ["public"]},
            top_k=4,
        )
        detail = retrieval_audit_detail(result["audit_id"])
        self.assertEqual(detail["status"], "ready")
        self.assertEqual(detail["audit"]["audit_id"], result["audit_id"])
        self.assertIsInstance(detail["audit"]["backend_results"], dict)
        self.assertGreaterEqual(len(detail["results"]), 1)
        self.assertIn("found_by", detail["results"][0])

        repair = generate_codex_repair_from_audit(
            result["audit_id"],
            expected_behavior="The strongest coverage maintenance paper should appear in evidence.",
        )
        self.assertEqual(repair["status"], "ready")
        self.assertTrue(Path(repair["file_path"]).exists())
        self.assertIn("Suggested Codex Repair Task", repair["content"])


if __name__ == "__main__":
    unittest.main()
