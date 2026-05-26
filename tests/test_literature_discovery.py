from __future__ import annotations

import os
import unittest
from urllib.parse import parse_qs, urlparse


class LiteratureDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_env = {key: os.environ.get(key) for key in ["TRANSLATION_LLM_BASE_URL", "HYMT2_OPENAI_BASE_URL"]}
        os.environ.pop("TRANSLATION_LLM_BASE_URL", None)
        os.environ.pop("HYMT2_OPENAI_BASE_URL", None)

    def tearDown(self) -> None:
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _payload(self) -> dict:
        return {
            "meta": {"count": 2},
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "doi": "https://doi.org/10.1234/RAG.1",
                    "title": "Multimodal Retrieval for Grounded Literature Review",
                    "publication_year": 2026,
                    "publication_date": "2026-01-12",
                    "type": "article",
                    "language": "en",
                    "cited_by_count": 9,
                    "primary_location": {
                        "landing_page_url": "https://publisher.test/rag1",
                        "pdf_url": "https://publisher.test/rag1.pdf",
                        "source": {"display_name": "Journal of RAG Systems", "issn_l": "1234-5678", "issn": ["1234-5678"]},
                    },
                    "best_oa_location": {},
                    "authorships": [{"author": {"display_name": "Ada Lovelace"}}, {"author": {"display_name": "Grace Hopper"}}],
                    "abstract_inverted_index": {"Multimodal": [0], "retrieval": [1], "improves": [2], "grounding.": [3]},
                },
                {
                    "id": "https://openalex.org/W2",
                    "doi": "https://doi.org/10.9999/OTHER.1",
                    "title": "Unrelated Journal Paper",
                    "publication_year": 2025,
                    "primary_location": {"source": {"display_name": "Other Venue", "issn_l": "0000-0000"}},
                    "authorships": [],
                    "abstract_inverted_index": {"Other": [0], "abstract.": [1]},
                },
            ],
        }

    def test_openalex_query_reconstructs_abstract_and_filters_journal(self) -> None:
        from app.literature_discovery import search_openalex

        seen_urls: list[str] = []

        def fetcher(url: str) -> dict:
            seen_urls.append(url)
            return self._payload()

        results, meta = search_openalex(
            query="multimodal RAG",
            keywords="citation grounding, evaluation",
            journals="Journal of RAG",
            year_from="2024",
            year_to="2026",
            max_results=5,
            fetcher=fetcher,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["doi"], "10.1234/RAG.1")
        self.assertEqual(results[0]["abstract_en"], "Multimodal retrieval improves grounding.")
        self.assertEqual(results[0]["authors"], ["Ada Lovelace", "Grace Hopper"])
        self.assertEqual(meta["filtered_count"], 1)
        qs = parse_qs(urlparse(seen_urls[0]).query)
        self.assertIn("has_doi:true", qs["filter"][0])
        self.assertIn("from_publication_date:2024-01-01", qs["filter"][0])
        self.assertIn("to_publication_date:2026-12-31", qs["filter"][0])
        self.assertIn("multimodal RAG", qs["search"][0])

    def test_discover_literature_bilingual_uses_injected_translator(self) -> None:
        from app.literature_discovery import discover_literature

        result = discover_literature(
            query="multimodal RAG",
            journals="Journal of RAG",
            max_results=3,
            language_mode="bilingual",
            fetcher=lambda _url: self._payload(),
            translator=lambda text: "中文译文：" + text,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["count"], 1)
        item = result["results"][0]
        self.assertEqual(item["translation_status"], "ready")
        self.assertIn("English:", item["abstract_display"])
        self.assertIn("中文译文", item["abstract_display"])
        self.assertEqual(result["translation"]["statuses"], ["ready"])

    def test_discover_literature_chinese_mode_falls_back_without_endpoint(self) -> None:
        from app.literature_discovery import discover_literature

        result = discover_literature(
            query="multimodal RAG",
            journals="Journal of RAG",
            max_results=3,
            language_mode="zh",
            fetcher=lambda _url: self._payload(),
        )
        item = result["results"][0]
        self.assertEqual(item["translation_status"], "not_configured")
        self.assertEqual(item["abstract_display"], item["abstract_en"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
