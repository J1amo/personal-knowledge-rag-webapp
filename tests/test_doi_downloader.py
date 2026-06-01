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
            DEFAULT_MANUAL_LOGIN_TIMEOUT_SECONDS,
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
        self.assertEqual(normal.manual_login_timeout_seconds, DEFAULT_MANUAL_LOGIN_TIMEOUT_SECONDS)
        self.assertTrue(normal.use_deepseek)
        self.assertFalse(normal.campus_session_mode)
        self.assertFalse(resolve_settings({"use_deepseek": False}).use_deepseek)
        self.assertNotEqual(normal.page_action_wait_min, normal.article_delay_min)
        self.assertEqual(resolve_settings({"manual_login_timeout_seconds": 5}).manual_login_timeout_seconds, 30)
        self.assertEqual(resolve_settings({"manual_login_timeout_seconds": 7200}).manual_login_timeout_seconds, 3600)
        campus_session = resolve_settings({"campus_session_mode": True})
        self.assertTrue(campus_session.campus_session_mode)
        self.assertTrue(campus_session.headed)
        self.assertTrue(campus_session.allow_manual_login)

        fast = resolve_settings({"fast_mode": True, "max_items": 99})
        self.assertTrue(fast.fast_mode)
        self.assertEqual(fast.max_items, FAST_MAX_ITEMS)
        self.assertEqual(fast.article_delay_min, FAST_ARTICLE_DELAY_MIN)
        self.assertEqual(fast.article_delay_max, FAST_ARTICLE_DELAY_MAX)

    def test_source_group_throttle_skips_different_sites(self) -> None:
        from app.doi_downloader import DoiDownloadSettings, PlaywrightDownloadSession

        sleeps: list[float] = []
        session = PlaywrightDownloadSession(
            DoiDownloadSettings(out_dir=str(self.root / "papers")),
            sleeper=sleeps.append,
        )
        with patch("app.doi_downloader.random.uniform", return_value=10.0), patch(
            "app.doi_downloader.time.monotonic",
            side_effect=[100.0, 101.0, 102.0, 110.0],
        ):
            session._throttle_source_group({"href": "https://acs.test/article-1"}, "https://acs.test/article-1")
            session._throttle_source_group({"href": "https://elsevier.test/article-1"}, "https://elsevier.test/article-1")
            session._throttle_source_group({"href": "https://acs.test/article-2"}, "https://acs.test/article-2")

        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 8.0)

    def test_access_block_classification(self) -> None:
        from app.doi_downloader import classify_access_block

        self.assertEqual(classify_access_block(403, "https://publisher.test/article", "")[0], "blocked_by_access")
        self.assertEqual(
            classify_access_block(200, "https://pubs.acs.org/article", "Access is not provided via University of Tsukuba")[0],
            "blocked_by_access",
        )
        self.assertEqual(classify_access_block(429, "https://publisher.test/article", "")[0], "blocked_by_rate_limit")
        self.assertEqual(classify_access_block(200, "https://publisher.test", "Please complete CAPTCHA")[0], "blocked_by_captcha")
        self.assertEqual(
            classify_access_block(200, "https://pubs.acs.org/article", "Access through institution Log In Open PDF")[0],
            None,
        )
        self.assertEqual(
            classify_access_block(
                200,
                "https://pubs.rsc.org/en/content/articlelanding/2013/nr/c3nr33738c/unauth",
                '"isAccessibleForFree": "False"',
            )[0],
            "blocked_by_access",
        )
        self.assertEqual(
            classify_access_block(
                200,
                "https://pubs.acs.org/article",
                "pubs.acs.org 正在进行安全验证 正在验证 由 Cloudflare 提供的性能和安全服务",
            )[0],
            "blocked_by_captcha",
        )
        self.assertEqual(
            classify_access_block(
                200,
                "https://validate.perfdrive.com/fb803c746e9148689b3984a31fccd902/?ssk=botmanager_support@example.com",
                "",
            )[0],
            "blocked_by_captcha",
        )
        self.assertEqual(
            classify_access_block(
                200,
                "https://idp.account.tsukuba.ac.jp/idp/profile/SAML2/Redirect/SSO?execution=e1s2",
                "",
            )[0],
            "needs_login",
        )
        self.assertEqual(classify_access_block(200, "https://idp.test", "Shibboleth sign in MFA required")[0], "needs_login")
        self.assertIsNone(classify_access_block(200, "https://publisher.test", "Access through your organization")[0])
        self.assertIsNone(classify_access_block(200, "https://publisher.test", "remote access configuration")[0])

    def test_manual_access_wait_covers_institution_access_pages(self) -> None:
        from app.doi_downloader import resolve_settings, should_wait_for_manual_access

        enabled = resolve_settings({"headed": True, "allow_manual_login": True})
        disabled = resolve_settings({"headed": True, "allow_manual_login": False})
        background = resolve_settings({"headed": False, "allow_manual_login": True})
        campus_session = resolve_settings({"campus_session_mode": True})

        self.assertTrue(should_wait_for_manual_access("needs_login", enabled))
        self.assertTrue(should_wait_for_manual_access("blocked_by_access", enabled))
        self.assertFalse(should_wait_for_manual_access("blocked_by_captcha", enabled))
        self.assertTrue(should_wait_for_manual_access("blocked_by_captcha", campus_session))
        self.assertFalse(should_wait_for_manual_access("blocked_by_access", disabled))
        self.assertFalse(should_wait_for_manual_access("needs_login", background))
        self.assertFalse(should_wait_for_manual_access("blocked_by_access", background))

    def test_campus_only_platform_suppresses_manual_login_wait(self) -> None:
        from app.doi_downloader import apply_candidate_manual_wait_policy, resolve_settings

        candidate = {
            "authorized_platform": {
                "name": "AIP Journals Complete",
                "campus_only": True,
            }
        }
        state, reason, diagnostics = apply_candidate_manual_wait_policy(
            "needs_login",
            "Login, MFA, Shibboleth, or EZproxy page detected",
            {"matched_terms": ["sign in via your institution"]},
            candidate,
        )

        self.assertEqual(state, "blocked_by_access")
        self.assertIn("校区内限定", reason or "")
        self.assertTrue(diagnostics["manual_wait_suppressed"])
        self.assertEqual(diagnostics["manual_wait_suppressed_reason"], "campus_only_platform")

        state, _reason, diagnostics = apply_candidate_manual_wait_policy(
            "blocked_by_captcha",
            "CAPTCHA, bot check, or security verification detected",
            {"matched_terms": ["recaptcha"]},
            candidate,
        )

        self.assertEqual(state, "blocked_by_access")
        self.assertTrue(diagnostics["manual_wait_suppressed"])

        state, reason, diagnostics = apply_candidate_manual_wait_policy(
            "needs_login",
            "Login, MFA, Shibboleth, or EZproxy page detected",
            {"matched_terms": ["sign in via your institution"]},
            candidate,
            resolve_settings({"campus_session_mode": True}),
        )

        self.assertEqual(state, "needs_login")
        self.assertIn("Login", reason or "")
        self.assertFalse(diagnostics["manual_wait_suppressed"])
        self.assertTrue(diagnostics["campus_session_mode"])

    def test_campus_only_platform_preflight_blocks_browser_collision(self) -> None:
        from app.doi_downloader import preflight_candidate_block_attempt, resolve_settings

        candidate = {
            "href": "https://doi.org/10.1063/1.4895030",
            "policy": {"open_access": False},
            "authorized_platform": {"name": "AIP Journals Complete", "campus_only": True},
        }
        attempt = preflight_candidate_block_attempt(candidate)

        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.status, "blocked_by_access")
        self.assertIn("先不打开自动化浏览器", attempt.failure_reason or "")
        self.assertTrue((attempt.diagnostics or {})["preflight_blocked"])
        self.assertIsNone(preflight_candidate_block_attempt(candidate, resolve_settings({"campus_session_mode": True})))

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
        sidecar = Path(saved["metadata_path"])
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["status"] = "rejected_non_article_pdf"
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(find_existing_download("10.1234/test", out_dir))

    def test_unauthorized_preflight_does_not_require_playwright(self) -> None:
        from app.doi_downloader import run_doi_download_job

        with patch("app.doi_downloader.find_spec", return_value=None):
            result = run_doi_download_job(
                "10.1234/missing-playwright",
                {"out_dir": str(self.root / "papers"), "max_items": 1},
                metadata_fetcher=lambda doi: {"doi": doi, "title": "Missing Playwright"},
                sleeper=lambda _seconds: None,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["items"][0]["status"], "skipped_not_authorized")
        self.assertNotIn("Playwright", result["items"][0]["failure_reason"])

    def test_playwright_missing_is_recorded_only_when_browser_candidate_is_needed(self) -> None:
        from app.doi_downloader import run_doi_download_job

        candidates = [
            {
                "href": "https://publisher.test/article",
                "text": "Full text",
                "source": "serials_solutions",
                "priority": 10,
                "publisher_domain": "publisher.test",
                "policy": {"allowed": True},
            }
        ]
        with patch("app.doi_downloader.find_spec", return_value=None), patch(
            "app.doi_downloader.doi_landing_candidates", return_value=candidates
        ):
            result = run_doi_download_job(
                "10.1234/needs-browser",
                {"out_dir": str(self.root / "papers"), "max_items": 1},
                metadata_fetcher=lambda doi: {"doi": doi, "title": "Needs Browser"},
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
        self.assertEqual(result["items"][2]["status"], "stopped")
        self.assertEqual(result["items"][2]["doi"], "10.1234/not-processed")
        self.assertIn("Not processed because the job stopped", result["items"][2]["failure_reason"])
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["summary"]["processed_count"], 2)
        self.assertEqual(result["summary"]["unprocessed_count"], 1)
        self.assertEqual(result["summary"]["completed_batches"], 1)
        self.assertEqual(result["summary"]["status_counts"]["stopped"], 1)
        self.assertTrue(result["items"][0]["ingestion_source_id"].startswith("src_"))
        with connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM doi_download_jobs").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM doi_download_items").fetchone()["n"], 3)
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"], 1)

    def test_pdf_link_extractor_ignores_javascript_void_links(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "app" / "doi_downloader.py").read_text(encoding="utf-8")
        self.assertIn("lowerHref.startsWith('javascript:')", source)
        self.assertIn("lowerHref.startsWith('mailto:')", source)
        self.assertIn("ieeexplore\\\\.ieee\\\\.org\\\\/document", source)
        self.assertIn("stamp/stamp.jsp?tp=&arnumber=", source)
        self.assertIn("contentplatform_userguide", source)
        self.assertIn("wp-content/uploads", source)
        self.assertIn("sciencedirect_pdf_download", source)
        self.assertIn("current_pdf_like_url", source)

    def test_sciencedirect_html_state_yields_pdf_links(self) -> None:
        from app.doi_downloader import pdf_links_from_html_snapshot

        html = """
        <script>
        window.__PRELOADED_STATE__ = {"article":{
          "openManuscriptUrl":"/science/article/am/pii/S0038110121001684",
          "pdfDownload":{"urlMetadata":{
            "queryParams":{"md5":"abc123","pid":"1-s2.0-S0038110122003239-main.pdf"},
            "pii":"S0038110122003239","pdfExtension":"/pdfft","path":"science/article/pii"
          }}
        }};
        </script>
        """

        links = pdf_links_from_html_snapshot(html, "https://www-sciencedirect-com.tsukuba.idm.oclc.org/science/article/pii/S003")

        self.assertEqual(links[0]["href"], "https://www-sciencedirect-com.tsukuba.idm.oclc.org/science/article/am/pii/S0038110121001684")
        self.assertEqual(
            links[1]["href"],
            "https://www-sciencedirect-com.tsukuba.idm.oclc.org/science/article/pii/S0038110122003239/pdfft?md5=abc123&pid=1-s2.0-S0038110122003239-main.pdf",
        )

    def test_html_snapshot_extracts_public_pdf_meta_and_links(self) -> None:
        from app.doi_downloader import pdf_links_from_html_snapshot

        html = """
        <meta name="citation_pdf_url" content="/articles/PMC4837198/pdf/11671_2016_Article_1396.pdf">
        <a href="/download/article.pdf">Download PDF</a>
        <a href="javascript:void(0)">PDF</a>
        """

        links = pdf_links_from_html_snapshot(html, "https://pmc.ncbi.nlm.nih.gov/articles/PMC4837198/")

        self.assertEqual(
            [item["href"] for item in links],
            [
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC4837198/pdf/11671_2016_Article_1396.pdf",
                "https://pmc.ncbi.nlm.nih.gov/download/article.pdf",
            ],
        )

    def test_serials_solutions_candidates_keep_only_fulltext_routes(self) -> None:
        from app.doi_downloader import parse_serials_solutions_candidates, serials_solutions_lookup_url

        lookup_url = serials_solutions_lookup_url("10.1021/acs.nanolett.3c04180")
        self.assertIn("SS_LibHash=JN2XS2WB8U", lookup_url)
        self.assertIn("sid=sersol%3AuniqueIDQuery", lookup_url)

        html = """
        <a href="./log?L=JN2XS2WB8U&amp;U=https%3A%2F%2Ftsukuba.idm.oclc.org%2Flogin%3Furl%3Dhttps%3A%2F%2Fpubs.acs.org%2Fdoi%2F10.1021%2Facs.nanolett.3c04180">フルテキストを見る</a>
        <a href="https://hal.science/hal-04296517v1/document">オープンアクセスバージョンを入手</a>
        <a href="http://www.refworks.com/express/expressimport.asp">RefWorks</a>
        <a href="?SS_Page=refiner&amp;SS_doi=10.1021/acs.nanolett.3c04180">書誌情報を変更して再検索する</a>
        <a href="https://tsukuba.idm.oclc.org/login?url=https://ulrichsweb.serialssolutions.com/api/openurl?issn=15306984">Ulrichsweb.com</a>
        """

        candidates = parse_serials_solutions_candidates(html)
        self.assertEqual([item["text"] for item in candidates], ["フルテキストを見る", "オープンアクセスバージョンを入手"])
        self.assertEqual(candidates[0]["publisher_domain"], "tsukuba.idm.oclc.org")
        self.assertEqual(candidates[1]["publisher_domain"], "hal.science")

    def test_serials_solutions_xml_candidates_use_authorized_article_links(self) -> None:
        from app.doi_downloader import (
            parse_serials_solutions_xml_candidates,
            serials_solutions_xml_lookup_url,
        )

        lookup_url = serials_solutions_xml_lookup_url("10.1021/acs.nanolett.5b04038")
        self.assertTrue(lookup_url.startswith("http://jn2xs2wb8u.openurl.xml.serialssolutions.com/openurlxml?"))
        self.assertIn("rft_id=info%3Adoi%2F10.1021%2Facs.nanolett.5b04038", lookup_url)

        xml = """
        <ssopenurl:openURLResponse
          xmlns:dc="http://purl.org/dc/elements/1.1/"
          xmlns:ssopenurl="http://xml.serialssolutions.com/ns/openurl/v1.0">
          <ssopenurl:results>
            <ssopenurl:result format="journal">
              <ssopenurl:citation><dc:title>Article</dc:title></ssopenurl:citation>
              <ssopenurl:linkGroups>
                <ssopenurl:linkGroup type="holding">
                  <ssopenurl:holdingData>
                    <ssopenurl:providerName>American Chemical Society</ssopenurl:providerName>
                    <ssopenurl:databaseName>American Chemical Society Journals</ssopenurl:databaseName>
                  </ssopenurl:holdingData>
                  <ssopenurl:url type="source">https://tsukuba.idm.oclc.org/login?url=https://pubs.acs.org/action/showPublications</ssopenurl:url>
                  <ssopenurl:url type="article">https://tsukuba.idm.oclc.org/login?url=https://pubs.acs.org/doi/10.1021/acs.nanolett.5b04038</ssopenurl:url>
                </ssopenurl:linkGroup>
              </ssopenurl:linkGroups>
            </ssopenurl:result>
          </ssopenurl:results>
        </ssopenurl:openURLResponse>
        """

        candidates = parse_serials_solutions_xml_candidates(xml)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "serials_solutions_xml")
        self.assertEqual(candidates[0]["publisher_domain"], "tsukuba.idm.oclc.org")
        self.assertEqual(candidates[0]["target_domain"], "pubs.acs.org")
        self.assertEqual(candidates[0]["serials_database"], "American Chemical Society Journals")

    def test_serials_solutions_xml_unwraps_public_article_links(self) -> None:
        from app.doi_downloader import parse_serials_solutions_xml_candidates

        xml = """
        <ssopenurl:openURLResponse xmlns:ssopenurl="http://xml.serialssolutions.com/ns/openurl/v1.0">
          <ssopenurl:linkGroup type="holding">
            <ssopenurl:holdingData>
              <ssopenurl:providerName>National Library of Medicine</ssopenurl:providerName>
              <ssopenurl:databaseName>PubMed Central</ssopenurl:databaseName>
            </ssopenurl:holdingData>
            <ssopenurl:url type="article">https://tsukuba.idm.oclc.org/login?url=https://www.ncbi.nlm.nih.gov/pmc/articles/doi/10.1186/s11671-016-1396-7</ssopenurl:url>
          </ssopenurl:linkGroup>
        </ssopenurl:openURLResponse>
        """

        candidates = parse_serials_solutions_xml_candidates(xml)
        self.assertEqual(
            candidates[0]["href"],
            "https://www.ncbi.nlm.nih.gov/pmc/articles/doi/10.1186/s11671-016-1396-7",
        )
        self.assertEqual(candidates[0]["priority"], 1)
        self.assertEqual(candidates[0]["publisher_domain"], "ncbi.nlm.nih.gov")

    def test_serials_solutions_unwraps_public_fulltext_proxy_links(self) -> None:
        from app.doi_downloader import parse_serials_solutions_candidates

        html = """
        <a href="./log?L=JN2XS2WB8U&amp;U=https%3A%2F%2Ftsukuba.idm.oclc.org%2Flogin%3Furl%3Dhttps%3A%2F%2Fwww.proquest.com%2Fdocview%2F1782088926">フルテキストを見る</a>
        <a href="./log?L=JN2XS2WB8U&amp;U=https%3A%2F%2Ftsukuba.idm.oclc.org%2Flogin%3Furl%3Dhttps%3A%2F%2Fwww.ncbi.nlm.nih.gov%2Fpmc%2Farticles%2Fdoi%2F10.1186%2Fs11671-016-1396-7">フルテキストを見る</a>
        """

        candidates = parse_serials_solutions_candidates(html)
        self.assertEqual(candidates[0]["href"], "https://www.ncbi.nlm.nih.gov/pmc/articles/doi/10.1186/s11671-016-1396-7")
        self.assertEqual(candidates[0]["publisher_domain"], "ncbi.nlm.nih.gov")

    def test_hal_api_file_candidates_extract_public_pdf_file(self) -> None:
        from app.doi_downloader import hal_api_lookup_url, parse_hal_api_file_candidates

        candidate = {
            "href": "https://hal.science/hal-04296517v1/document",
            "text": "オープンアクセスバージョンを入手",
            "source": "serials_solutions",
            "priority": 15,
            "publisher_domain": "hal.science",
        }
        payload = {
            "response": {
                "docs": [
                    {
                        "files_s": [
                            "https://hal.science/hal-04296517/file/FINAL%20VERSION.pdf",
                        ]
                    }
                ]
            }
        }

        self.assertIn("halId_s%3Ahal-04296517", hal_api_lookup_url(candidate["href"]) or "")
        results = parse_hal_api_file_candidates(candidate, json.dumps(payload))
        self.assertEqual(results[0]["source"], "hal_api")
        self.assertEqual(results[0]["href"], "https://hal.science/hal-04296517/file/FINAL%20VERSION.pdf")

    def test_openalex_candidates_extract_open_access_pdf_urls(self) -> None:
        from app.doi_downloader import parse_openalex_candidates

        payload = {
            "open_access": {"oa_url": "https://repository.test/article"},
            "primary_location": {"pdf_url": "https://repository.test/article.pdf"},
            "locations": [
                {"landing_page_url": "https://repository.test/article"},
                {"pdf_url": "https://publisher.test/paywalled.pdf"},
            ],
        }

        results = parse_openalex_candidates("10.1234/open", json.dumps(payload))

        self.assertEqual(results[0]["href"], "https://repository.test/article.pdf")
        self.assertEqual(results[0]["source"], "openalex")
        self.assertEqual(len({item["href"] for item in results}), len(results))

    def test_openalex_candidates_ignore_doi_landing_pages(self) -> None:
        from app.doi_downloader import parse_openalex_candidates

        payload = {
            "open_access": {"oa_url": "https://doi.org/10.1109/ted.2023.3268249"},
            "primary_location": {"landing_page_url": "https://doi.org/10.1109/ted.2023.3268249"},
            "locations": [
                {"landing_page_url": "https://ieeexplore.ieee.org/document/101234"},
                {"pdf_url": "https://hal.science/hal-04296517/file/FINAL%20VERSION.pdf"},
            ],
        }

        results = parse_openalex_candidates("10.1109/ted.2023.3268249", json.dumps(payload))

        self.assertEqual(
            [item["href"] for item in results],
            ["https://hal.science/hal-04296517/file/FINAL%20VERSION.pdf"],
        )

    def test_doi_landing_candidates_append_direct_fallback(self) -> None:
        from app.doi_downloader import doi_landing_candidates

        with patch(
            "app.doi_downloader.fetch_serials_solutions_candidates",
            return_value=[
                {
                    "href": "https://tsukuba.idm.oclc.org/login?url=https://pubs.acs.org/doi/10.1234/test",
                    "text": "フルテキストを見る",
                    "source": "serials_solutions",
                    "priority": 10,
                    "publisher_domain": "tsukuba.idm.oclc.org",
                }
            ],
        ):
            candidates = doi_landing_candidates(
                "10.1234/test",
                {"publisher": "American Chemical Society"},
                include_direct=True,
            )

        self.assertEqual(candidates[0]["source"], "serials_solutions")
        self.assertEqual(candidates[-1]["href"], "https://doi.org/10.1234/test")
        self.assertEqual(candidates[-1]["authorized_platform"]["name"], "American Chemical Society Journals")

    def test_doi_landing_candidates_skip_non_authorized_direct_fallback(self) -> None:
        from app.doi_downloader import doi_landing_candidates, no_authorized_landing_attempt

        with patch("app.doi_downloader.fetch_serials_solutions_candidates", return_value=[]), patch(
            "app.doi_downloader.fetch_openalex_candidates", return_value=[]
        ):
            candidates = doi_landing_candidates(
                "10.1109/ted.2023.3268249",
                {"publisher": "Institute of Electrical and Electronics Engineers (IEEE)"},
            )

        self.assertEqual(candidates, [])
        attempt = no_authorized_landing_attempt(
            "10.1109/ted.2023.3268249",
            {"publisher": "Institute of Electrical and Electronics Engineers (IEEE)"},
        )
        self.assertEqual(attempt.status, "skipped_not_authorized")
        self.assertIn("不在筑波大学授权数据库列表", attempt.failure_reason or "")

    def test_doi_landing_candidates_do_not_direct_browser_without_resolver_entry(self) -> None:
        from app.doi_downloader import doi_landing_candidates

        with patch("app.doi_downloader.fetch_serials_solutions_candidates", return_value=[]), patch(
            "app.doi_downloader.fetch_openalex_candidates", return_value=[]
        ):
            candidates = doi_landing_candidates(
                "10.1088/1361-6463/ad4716",
                {"publisher": "IOP Publishing"},
            )

        self.assertEqual(candidates, [])

    def test_doi_landing_candidates_prioritize_known_springer_open_pdf(self) -> None:
        from app.doi_downloader import doi_landing_candidates

        with patch("app.doi_downloader.fetch_serials_solutions_candidates", return_value=[]):
            candidates = doi_landing_candidates(
                "10.1186/s11671-016-1396-7",
                {"publisher": "Springer Science and Business Media LLC"},
            )

        self.assertEqual(candidates[0]["source"], "known_public_pdf")
        self.assertEqual(
            candidates[0]["href"],
            "https://link.springer.com/content/pdf/10.1186/s11671-016-1396-7.pdf",
        )

    def test_doi_landing_candidates_use_openalex_before_direct_browser(self) -> None:
        from app.doi_downloader import doi_landing_candidates

        with patch("app.doi_downloader.fetch_serials_solutions_candidates", return_value=[]), patch(
            "app.doi_downloader.fetch_openalex_candidates",
            return_value=[
                {
                    "href": "https://repository.test/article.pdf",
                    "text": "OpenAlex PDF URL",
                    "source": "openalex",
                    "priority": 12,
                    "publisher_domain": "repository.test",
                }
            ],
        ):
            candidates = doi_landing_candidates("10.1234/open", {"publisher": "Unknown Publisher"})

        self.assertEqual(candidates[0]["source"], "openalex")
        self.assertTrue(candidates[0]["policy"]["open_access"])
        self.assertEqual([item["source"] for item in candidates], ["openalex"])

    def test_doi_landing_candidates_keep_open_access_even_when_publisher_not_authorized(self) -> None:
        from app.doi_downloader import doi_landing_candidates

        with patch(
            "app.doi_downloader.fetch_serials_solutions_candidates",
            return_value=[
                {
                    "href": "https://hal.science/hal-04296517/file/FINAL%20VERSION.pdf",
                    "text": "オープンアクセスバージョンを入手 file",
                    "source": "hal_api",
                    "priority": 14,
                    "publisher_domain": "hal.science",
                }
            ],
        ):
            candidates = doi_landing_candidates(
                "10.1109/ted.2023.3321277",
                {"publisher": "Institute of Electrical and Electronics Engineers (IEEE)"},
            )

        self.assertEqual(candidates[0]["source"], "hal_api")
        self.assertTrue(candidates[0]["policy"]["open_access"])
        self.assertEqual([item["source"] for item in candidates], ["hal_api"])

    def test_doi_landing_candidates_prefer_tsukuba_sciencedirect_proxy(self) -> None:
        from app.doi_downloader import doi_landing_candidates, tsukuba_proxy_url

        self.assertEqual(
            tsukuba_proxy_url("https://www.sciencedirect.com/science/article/abs/pii/S0038110116303094?via%3Dihub"),
            "https://www-sciencedirect-com.tsukuba.idm.oclc.org/science/article/abs/pii/S0038110116303094?via%3Dihub",
        )
        with patch(
            "app.doi_downloader.fetch_serials_solutions_candidates",
            return_value=[
                {
                    "href": "https://www.sciencedirect.com/science/article/abs/pii/S0038110116303094?via%3Dihub",
                    "text": "Full text",
                    "source": "serials_solutions",
                    "priority": 10,
                    "publisher_domain": "www.sciencedirect.com",
                }
            ],
        ):
            candidates = doi_landing_candidates("10.1016/j.sse.2016.12.008", {"publisher": "Elsevier"})

        self.assertEqual(candidates[0]["publisher_domain"], "www-sciencedirect-com.tsukuba.idm.oclc.org")
        self.assertEqual(candidates[0]["proxied_from"], candidates[1]["href"])

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

    def test_login_required_item_continues_batch(self) -> None:
        from app.doi_downloader import DownloadAttempt, run_doi_download_job

        pdf_path = self.root / "mock.pdf"
        make_pdf(pdf_path, "Mock DOI PDF")

        def fake_runner(doi, _settings, _metadata, _artifacts_dir):
            if doi.endswith("login"):
                return DownloadAttempt(
                    status="needs_login",
                    landing_url="https://tsukuba.idm.oclc.org/login",
                    publisher_domain="tsukuba.idm.oclc.org",
                    failure_reason="Login required",
                )
            return DownloadAttempt(
                status="downloaded",
                landing_url=f"https://publisher.test/{doi}",
                publisher_domain="publisher.test",
                pdf_url=f"https://publisher.test/{doi}.pdf",
                pdf_bytes=pdf_path.read_bytes(),
            )

        result = run_doi_download_job(
            "10.1234/login\n10.1234/downloaded",
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
        self.assertEqual([item["status"] for item in result["items"]], ["needs_login", "downloaded"])
        self.assertIsNone(result["summary"]["stopped_reason"])
        self.assertIn("needs_login", result["summary"]["continue_item_statuses"])

    def test_verification_queue_retries_latest_login_items(self) -> None:
        from app.doi_downloader import (
            DownloadAttempt,
            doi_downloader_status,
            list_doi_verification_queue,
            run_doi_download_job,
            run_doi_verification_queue_job,
        )

        pdf_path = self.root / "mock.pdf"
        make_pdf(pdf_path, "Mock DOI PDF")

        def first_runner(doi, _settings, _metadata, _artifacts_dir):
            if doi.endswith("login"):
                return DownloadAttempt(
                    status="needs_login",
                    landing_url="https://tsukuba.idm.oclc.org/login",
                    publisher_domain="tsukuba.idm.oclc.org",
                    failure_reason="Login required",
                )
            return DownloadAttempt(
                status="downloaded",
                landing_url=f"https://publisher.test/{doi}",
                publisher_domain="publisher.test",
                pdf_url=f"https://publisher.test/{doi}.pdf",
                pdf_bytes=pdf_path.read_bytes(),
            )

        metadata_fetcher = lambda doi: {
            "doi": doi,
            "title": "Mock DOI PDF",
            "authors": ["Grace Hopper"],
            "year": 2026,
            "publisher": "publisher.test",
        }

        run_doi_download_job(
            "10.1234/login\n10.1234/downloaded",
            {"out_dir": str(self.root / "papers"), "max_items": 2},
            browser_runner=first_runner,
            metadata_fetcher=metadata_fetcher,
            sleeper=lambda _seconds: None,
        )

        queue = list_doi_verification_queue()
        self.assertEqual([item["doi"] for item in queue], ["10.1234/login"])
        self.assertEqual(queue[0]["queue_action"], "manual_login_then_retry")
        self.assertTrue(queue[0]["retry_eligible"])
        self.assertEqual(
            doi_downloader_status()["access_session"]["mode"],
            "playwright_persistent_context",
        )
        self.assertFalse(
            doi_downloader_status()["access_session"]["codex_in_app_browser"]["downloads_supported"],
        )

        def retry_runner(doi, _settings, _metadata, _artifacts_dir):
            return DownloadAttempt(
                status="downloaded",
                landing_url=f"https://publisher.test/{doi}",
                publisher_domain="publisher.test",
                pdf_url=f"https://publisher.test/{doi}.pdf",
                pdf_bytes=pdf_path.read_bytes(),
            )

        retry = run_doi_verification_queue_job(
            {"out_dir": str(self.root / "papers"), "max_items": 2},
            browser_runner=retry_runner,
            metadata_fetcher=metadata_fetcher,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(retry["status"], "ready")
        self.assertEqual(retry["verification_queue"]["retry_count"], 1)
        self.assertEqual(retry["items"][0]["doi"], "10.1234/login")
        self.assertEqual(retry["items"][0]["status"], "downloaded")
        self.assertEqual(list_doi_verification_queue(), [])

    def test_campus_session_mode_retries_captcha_queue(self) -> None:
        from app.doi_downloader import (
            DownloadAttempt,
            list_doi_verification_queue,
            run_doi_download_job,
            run_doi_verification_queue_job,
        )

        pdf_path = self.root / "mock.pdf"
        make_pdf(pdf_path, "Mock DOI PDF")

        metadata_fetcher = lambda doi: {
            "doi": doi,
            "title": "Mock DOI PDF",
            "authors": ["Grace Hopper"],
            "year": 2026,
            "publisher": "publisher.test",
        }

        def captcha_runner(doi, _settings, _metadata, _artifacts_dir):
            return DownloadAttempt(
                status="blocked_by_captcha",
                landing_url=f"https://publisher.test/{doi}/security",
                publisher_domain="publisher.test",
                failure_reason="CAPTCHA required",
            )

        run_doi_download_job(
            "10.1234/captcha",
            {"out_dir": str(self.root / "papers")},
            browser_runner=captcha_runner,
            metadata_fetcher=metadata_fetcher,
            sleeper=lambda _seconds: None,
        )

        queue = list_doi_verification_queue()
        self.assertEqual([item["doi"] for item in queue], ["10.1234/captcha"])
        self.assertFalse(queue[0]["retry_eligible"])
        self.assertTrue(queue[0]["campus_session_retry_eligible"])

        default_retry = run_doi_verification_queue_job(
            {"out_dir": str(self.root / "papers")},
            browser_runner=captcha_runner,
            metadata_fetcher=metadata_fetcher,
            sleeper=lambda _seconds: None,
        )
        self.assertEqual(default_retry["status"], "noop")
        self.assertEqual(default_retry["summary"]["retry_count"], 0)

        seen_settings = []

        def solved_runner(doi, settings, _metadata, _artifacts_dir):
            seen_settings.append(settings)
            return DownloadAttempt(
                status="downloaded",
                landing_url=f"https://publisher.test/{doi}",
                publisher_domain="publisher.test",
                pdf_url=f"https://publisher.test/{doi}.pdf",
                pdf_bytes=pdf_path.read_bytes(),
            )

        campus_retry = run_doi_verification_queue_job(
            {"out_dir": str(self.root / "papers"), "campus_session_mode": True},
            browser_runner=solved_runner,
            metadata_fetcher=metadata_fetcher,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(campus_retry["status"], "ready")
        self.assertEqual(campus_retry["verification_queue"]["retry_count"], 1)
        self.assertTrue(campus_retry["verification_queue"]["campus_session_mode"])
        self.assertTrue(seen_settings[0].campus_session_mode)
        self.assertTrue(seen_settings[0].headed)
        self.assertTrue(seen_settings[0].allow_manual_login)
        self.assertEqual(list_doi_verification_queue(), [])

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
        self.assertEqual(len(sleeps), 0)


if __name__ == "__main__":
    unittest.main()
