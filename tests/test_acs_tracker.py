from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class AcsTrackerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["PKB_DATA_DIR"] = str(root / "data")
        os.environ["PKB_DB_DIR"] = str(root / "db")
        os.environ["PKB_INDEX_DIR"] = str(root / "indexes")
        os.environ["PKB_CACHE_DIR"] = str(root / "cache")
        os.environ["PKB_BACKUP_DIR"] = str(root / "backups")
        os.environ["PKB_OUTPUT_DIR"] = str(root / "outputs")
        os.environ["LOCAL_MODELS_DIR"] = str(root / "local_models")

        from app import config

        config.DATA_DIR = root / "data"
        config.RAW_DIR = config.DATA_DIR / "raw"
        config.DB_DIR = root / "db"
        config.INDEX_DIR = root / "indexes"
        config.CACHE_DIR = root / "cache"
        config.BACKUP_DIR = root / "backups"
        config.OUTPUT_DIR = root / "outputs"
        config.LOCAL_MODELS_DIR = root / "local_models"
        config.DB_PATH = config.DB_DIR / "knowledge.sqlite"

        from app.db import init_db

        init_db()
        self.root = root

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def payload(self) -> dict:
        return {
            "meta": {"count": 2},
            "results": [
                {
                    "id": "https://openalex.org/WACS1",
                    "doi": "https://doi.org/10.1021/acsnano.6c00001",
                    "title": "Gate-all-around Ge nanosheet transistor fabrication",
                    "publication_year": 2026,
                    "publication_date": "2026-02-01",
                    "type": "article",
                    "language": "en",
                    "cited_by_count": 5,
                    "primary_location": {
                        "landing_page_url": "https://pubs.acs.org/doi/10.1021/acsnano.6c00001",
                        "pdf_url": "",
                        "source": {"display_name": "ACS Nano", "issn_l": "1936-0851", "issn": ["1936-0851"]},
                    },
                    "best_oa_location": {},
                    "authorships": [{"author": {"display_name": "Ada Researcher"}}],
                    "abstract_inverted_index": {
                        "Selective": [0],
                        "etching": [1],
                        "enables": [2],
                        "Ge": [3],
                        "gate-all-around": [4],
                        "nanosheet": [5],
                        "devices.": [6],
                    },
                },
                {
                    "id": "https://openalex.org/WACS2",
                    "doi": "https://doi.org/10.1021/nl6c00002",
                    "title": "Background nanowire materials note",
                    "publication_year": 2025,
                    "publication_date": "2025-11-03",
                    "type": "article",
                    "language": "en",
                    "cited_by_count": 2,
                    "primary_location": {
                        "landing_page_url": "https://pubs.acs.org/doi/10.1021/nl6c00002",
                        "source": {"display_name": "Nano Letters", "issn_l": "1530-6984", "issn": ["1530-6984"]},
                    },
                    "authorships": [{"author": {"display_name": "Grace Engineer"}}],
                    "abstract_inverted_index": {"Nanowire": [0], "materials": [1], "survey.": [2]},
                },
            ],
        }

    def test_scoring_and_identity_are_stable(self) -> None:
        from app.acs_tracker import default_journals, get_profile, paper_key_for, score_candidate

        profile = get_profile("gaa_vertical_ge_si")
        item = {
            "title": "Gate-all-around Ge nanosheet transistor",
            "abstract_en": "Selective etching of SiGe enables Ge nanosheet fabrication.",
            "journal": "ACS Nano",
        }
        score = score_candidate(item, profile, default_journals())
        self.assertGreaterEqual(score["score"], 8)
        self.assertIn("Ge + gate-all-around", score["matched_keywords"])
        self.assertEqual(paper_key_for(doi="https://doi.org/10.1021/ABC"), "doi:10.1021/abc")
        self.assertTrue(paper_key_for(title="No DOI", url="https://example.test").startswith("title_url:"))

    def test_run_dedup_mark_and_export_without_network(self) -> None:
        from app.acs_tracker import export_digest, list_papers, mark_paper, run_tracker, tracker_status

        first = run_tracker(fetcher=lambda _url: self.payload(), max_results=5)
        second = run_tracker(fetcher=lambda _url: self.payload(), max_results=5)
        self.assertEqual(first["summary"]["created"], 2)
        self.assertEqual(second["summary"]["created"], 0)
        self.assertEqual(second["summary"]["updated"], 2)

        papers = list_papers()
        self.assertEqual(len(papers), 2)
        self.assertGreaterEqual(papers[0]["relevance_score"], papers[1]["relevance_score"])

        marked = mark_paper(doi="10.1021/acsnano.6c00001", status="must_read", notes="人工确认优先阅读")
        self.assertEqual(marked["paper_status"], "must_read")
        status = tracker_status()
        self.assertEqual(status["counts"]["must_read"], 1)

        md_path = self.root / "digest.md"
        csv_path = self.root / "papers.csv"
        md = export_digest(output_format="markdown", output_path=md_path)
        csv_result = export_digest(output_format="csv", output_path=csv_path)
        self.assertEqual(md["count"], 2)
        self.assertTrue(md_path.exists())
        self.assertIn("ACS Literature Digest", md_path.read_text(encoding="utf-8"))
        self.assertTrue(csv_path.exists())
        self.assertIn("Gate-all-around Ge nanosheet", csv_path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
