from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def make_pdf(path: Path, title: str = "DOI Downloader Test") -> None:
    import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(fitz.Rect(72, 72, 540, 720), title + "\n\nAuthorized DOI download fixture.", fontsize=11)
    doc.save(path)
    doc.close()


class DoiDownloaderTest(unittest.TestCase):
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

    def test_normalize_and_dedupe_dois(self) -> None:
        from app.doi_downloader import dedupe_dois, normalize_doi, parse_doi_list

        self.assertEqual(normalize_doi("DOI: 10.1234/ABC.Def"), "10.1234/abc.def")
        self.assertEqual(normalize_doi("https://doi.org/10.5555/Test-1"), "10.5555/test-1")
        values = parse_doi_list("10.1234/ABC.Def\nhttps://doi.org/10.1234/abc.def\n10.9999/foo")
        self.assertEqual(values, ["10.1234/abc.def", "10.9999/foo"])
        self.assertEqual(dedupe_dois(["10.1000/a", "doi:10.1000/A"]), ["10.1000/a"])

    def test_delay_policy_and_fast_mode_limits(self) -> None:
        from app.doi_downloader import (
            DEFAULT_ARTICLE_DELAY_MAX,
            DEFAULT_ARTICLE_DELAY_MIN,
            DEFAULT_PAGE_WAIT_MAX,
            DEFAULT_PAGE_WAIT_MIN,
            FAST_ARTICLE_DELAY_MAX,
            FAST_ARTICLE_DELAY_MIN,
            FAST_MAX_ITEMS,
            resolve_settings,
        )

        normal = resolve_settings({"max_items": 99})
        self.assertFalse(normal.fast_mode)
        self.assertEqual(normal.article_delay_min, DEFAULT_ARTICLE_DELAY_MIN)
        self.assertEqual(normal.article_delay_max, DEFAULT_ARTICLE_DELAY_MAX)
        self.assertEqual(normal.page_action_wait_min, DEFAULT_PAGE_WAIT_MIN)
        self.assertEqual(normal.page_action_wait_max, DEFAULT_PAGE_WAIT_MAX)
        self.assertNotEqual(normal.page_action_wait_min, normal.article_delay_min)

        fast = resolve_settings({"fast_mode": True, "max_items": 99})
        self.assertTrue(fast.fast_mode)
        self.assertEqual(fast.max_items, FAST_MAX_ITEMS)
        self.assertEqual(fast.article_delay_min, FAST_ARTICLE_DELAY_MIN)
        self.assertEqual(fast.article_delay_max, FAST_ARTICLE_DELAY_MAX)

    def test_access_block_classification(self) -> None:
        from app.doi_downloader import classify_access_block

        self.assertEqual(classify_access_block(403, "https://publisher.test/article", "")[0], "blocked_by_access")
        self.assertEqual(classify_access_block(429, "https://publisher.test/article", "")[0], "blocked_by_rate_limit")
        self.assertEqual(classify_access_block(200, "https://publisher.test", "Please complete CAPTCHA")[0], "blocked_by_captcha")
        self.assertEqual(
            classify_access_block(
                200,
                "https://pubs.acs.org/article",
                "pubs.acs.org 正在进行安全验证 正在验证 由 Cloudflare 提供的性能和安全服务",
            )[0],
            "blocked_by_captcha",
        )
        self.assertEqual(classify_access_block(200, "https://idp.test", "Shibboleth sign in MFA required")[0], "needs_login")

    def test_manual_access_wait_covers_institution_access_pages(self) -> None:
        from app.doi_downloader import resolve_settings, should_wait_for_manual_access

        enabled = resolve_settings({"headed": True, "allow_manual_login": True})
        disabled = resolve_settings({"headed": True, "allow_manual_login": False})

        self.assertTrue(should_wait_for_manual_access("needs_login", enabled))
        self.assertTrue(should_wait_for_manual_access("blocked_by_access", enabled))
        self.assertFalse(should_wait_for_manual_access("blocked_by_captcha", enabled))
        self.assertFalse(should_wait_for_manual_access("blocked_by_access", disabled))

    def test_pdf_save_metadata_sidecar_existing_skip_and_hash(self) -> None:
        from app.doi_downloader import find_existing_download, save_pdf_and_metadata

        pdf_path = self.root / "fixture.pdf"
        make_pdf(pdf_path)
        out_dir = self.root / "papers"
        saved = save_pdf_and_metadata(
            doi="10.1234/test",
            pdf_bytes=pdf_path.read_bytes(),
            out_dir=out_dir,
            metadata={"doi": "10.1234/test", "title": "A DOI Downloader Test", "authors": ["Ada Lovelace"], "year": 2026},
            landing_url="https://publisher.test/article",
            pdf_url="https://publisher.test/article.pdf",
            domain="publisher.test",
        )
        self.assertTrue(Path(saved["saved_path"]).exists())
        self.assertTrue(Path(saved["metadata_path"]).exists())
        self.assertEqual(len(saved["file_hash"]), 64)
        existing = find_existing_download("10.1234/test", out_dir)
        self.assertIsNotNone(existing)
        self.assertEqual(existing["saved_path"], saved["saved_path"])

    def test_job_records_playwright_missing_without_network(self) -> None:
        from app.doi_downloader import run_doi_download_job

        with patch("app.doi_downloader.find_spec", return_value=None):
            result = run_doi_download_job(
                "10.1234/missing-playwright",
                {"out_dir": str(self.root / "papers"), "max_items": 1},
                metadata_fetcher=lambda doi: {"doi": doi, "title": "Missing Playwright"},
                sleeper=lambda _seconds: None,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["items"][0]["status"], "failed")
        self.assertIn("Playwright", result["items"][0]["failure_reason"])

    def test_invalid_input_still_writes_job_log(self) -> None:
        from app.doi_downloader import run_doi_download_job

        result = run_doi_download_job(
            "not a doi",
            {"out_dir": str(self.root / "papers"), "max_items": 1},
            metadata_fetcher=lambda doi: {"doi": doi, "title": "Should Not Run"},
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["summary"]["message"], "No valid DOI supplied")
        self.assertTrue(Path(result["summary"]["log_path"]).exists())
        log_payload = json.loads(Path(result["summary"]["log_path"]).read_text(encoding="utf-8"))
        self.assertEqual(log_payload["summary"]["log_path"], result["summary"]["log_path"])

    def test_mock_download_auto_ingest_and_stop_condition(self) -> None:
        from app.db import connect
        from app.doi_downloader import DownloadAttempt, run_doi_download_job

        pdf_path = self.root / "mock.pdf"
        make_pdf(pdf_path, "Mock DOI PDF")

        def fake_runner(doi, _settings, _metadata, _artifacts_dir):
            if doi.endswith("blocked"):
                return DownloadAttempt(
                    status="blocked_by_rate_limit",
                    landing_url="https://publisher.test/blocked",
                    publisher_domain="publisher.test",
                    failure_reason="429 detected",
                )
            return DownloadAttempt(
                status="downloaded",
                landing_url="https://publisher.test/article",
                publisher_domain="publisher.test",
                pdf_url="https://publisher.test/article.pdf",
                pdf_bytes=pdf_path.read_bytes(),
            )

        result = run_doi_download_job(
            "10.1234/downloaded\n10.1234/blocked\n10.1234/not-processed",
            {"out_dir": str(self.root / "papers"), "max_items": 3, "auto_ingest": True},
            browser_runner=fake_runner,
            metadata_fetcher=lambda doi: {
                "doi": doi,
                "title": "Mock DOI PDF",
                "authors": ["Grace Hopper"],
                "year": 2026,
                "publisher": "publisher.test",
            },
            sleeper=lambda _seconds: None,
        )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["items"][0]["status"], "downloaded")
        self.assertEqual(result["items"][1]["status"], "blocked_by_rate_limit")
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(result["items"][0]["ingestion_source_id"].startswith("src_"))
        with connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM doi_download_jobs").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM doi_download_items").fetchone()["n"], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"], 1)

    def test_access_denied_item_continues_with_diagnostics(self) -> None:
        from app.doi_downloader import DownloadAttempt, run_doi_download_job

        pdf_path = self.root / "mock.pdf"
        make_pdf(pdf_path, "Mock DOI PDF")

        def fake_runner(doi, _settings, _metadata, _artifacts_dir):
            if doi.endswith("blocked"):
                return DownloadAttempt(
                    status="blocked_by_access",
                    landing_url="https://publisher.test/blocked",
                    publisher_domain="publisher.test",
                    failure_reason="Access denied or institutional access warning detected (signals: purchase access)",
                    diagnostics={"classification": "blocked_by_access", "matched_terms": ["purchase access"]},
                )
            return DownloadAttempt(
                status="downloaded",
                landing_url="https://publisher.test/article",
                publisher_domain="publisher.test",
                pdf_url="https://publisher.test/article.pdf",
                pdf_bytes=pdf_path.read_bytes(),
            )

        result = run_doi_download_job(
            "10.1234/blocked\n10.1234/downloaded",
            {"out_dir": str(self.root / "papers"), "max_items": 2},
            browser_runner=fake_runner,
            metadata_fetcher=lambda doi: {
                "doi": doi,
                "title": "Mock DOI PDF",
                "authors": ["Grace Hopper"],
                "year": 2026,
                "publisher": "publisher.test",
            },
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["items"][0]["status"], "blocked_by_access")
        self.assertEqual(result["items"][0]["diagnostics"]["matched_terms"], ["purchase access"])
        self.assertEqual(result["items"][1]["status"], "downloaded")
        self.assertEqual(result["summary"]["processed_count"], 2)
        self.assertEqual(result["summary"]["unprocessed_count"], 0)

    def test_max_items_is_batch_size_and_processes_full_list(self) -> None:
        from app.doi_downloader import DownloadAttempt, run_doi_download_job

        pdf_path = self.root / "mock.pdf"
        make_pdf(pdf_path, "Mock DOI PDF")
        sleeps = []

        def fake_runner(doi, _settings, _metadata, _artifacts_dir):
            return DownloadAttempt(
                status="downloaded",
                landing_url=f"https://publisher.test/{doi}",
                publisher_domain="publisher.test",
                pdf_url=f"https://publisher.test/{doi}.pdf",
                pdf_bytes=pdf_path.read_bytes(),
            )

        result = run_doi_download_job(
            "\n".join(
                [
                    "10.1234/item-1",
                    "10.1234/item-2",
                    "10.1234/item-3",
                    "10.1234/item-4",
                    "10.1234/item-5",
                ]
            ),
            {"out_dir": str(self.root / "papers"), "max_items": 2},
            browser_runner=fake_runner,
            metadata_fetcher=lambda doi: {
                "doi": doi,
                "title": "Mock DOI PDF",
                "authors": ["Grace Hopper"],
                "year": 2026,
                "publisher": "publisher.test",
            },
            sleeper=sleeps.append,
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(result["summary"]["requested_count"], 5)
        self.assertEqual(result["summary"]["processed_count"], 5)
        self.assertEqual(result["summary"]["unprocessed_count"], 0)
        self.assertEqual(result["summary"]["batch_size"], 2)
        self.assertEqual(result["summary"]["batch_count"], 3)
        self.assertEqual(result["summary"]["completed_batches"], 3)
        self.assertEqual([item["batch_index"] for item in result["items"]], [1, 1, 2, 2, 3])
        self.assertEqual(len(sleeps), 4)


if __name__ == "__main__":
    unittest.main()
