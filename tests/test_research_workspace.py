from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


def make_pdf(path: Path, title: str, body: str) -> None:
    import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 720),
        title + "\n\n" + body,
        fontsize=11,
        fontname="helv",
        align=0,
    )
    doc.save(path)
    doc.close()


class ResearchWorkspaceTest(unittest.TestCase):
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

    def _ingest_fixture_sources(self) -> tuple[str, str]:
        from app.ingest import ingest_file
        from app.indexes import rebuild_indexes
        from app import config

        paper_dir = self.root / "papers"
        paper_dir.mkdir()
        first = paper_dir / "project_alpha.pdf"
        second = paper_dir / "project_beta.pdf"
        make_pdf(first, "Alpha Evidence", "alpha-route baseline validation measurable photoconductive signal")
        make_pdf(second, "Beta Evidence", "beta-route unrelated archive thermal packaging note")
        first_result = ingest_file(first, domain="paper", topic="alpha", sensitivity="public")
        second_result = ingest_file(second, domain="paper", topic="beta", sensitivity="public")
        rebuild_indexes(index_names=[config.LOCAL_VECTOR_INDEX, config.BM25_INDEX, config.GRAPH_INDEX])
        return first_result.source_id, second_result.source_id

    def test_project_membership_and_scoped_retrieval(self) -> None:
        first_source, second_source = self._ingest_fixture_sources()

        from app.db import connect
        from app.research_projects import (
            add_source_to_project,
            create_project,
            list_project_sources,
            remove_source_from_project,
            retrieve_for_project,
        )

        first_project = create_project(name="Alpha Research", pack_id="gaa_vertical")
        second_project = create_project(name="Second Research", pack_id=None)
        add_source_to_project(first_project["project_id"], first_source, role="core_paper")
        add_source_to_project(second_project["project_id"], first_source, role="reference")

        self.assertEqual(len(list_project_sources(first_project["project_id"])), 1)
        self.assertEqual(len(list_project_sources(second_project["project_id"])), 1)

        remove_source_from_project(second_project["project_id"], first_source)
        self.assertEqual(list_project_sources(second_project["project_id"]), [])
        with connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"], 2)

        result = retrieve_for_project(
            first_project["project_id"],
            "alpha-route photoconductive baseline validation",
            retrieval_mode="strict_exhaustive",
            top_k=8,
        )
        self.assertTrue(result["evidence"])
        self.assertEqual({item["source_id"] for item in result["evidence"]}, {first_source})
        self.assertNotIn(second_source, {item["source_id"] for item in result["evidence"]})

    def test_project_pack_output_uses_project_scope(self) -> None:
        first_source, _second_source = self._ingest_fixture_sources()

        from app.output_studio import generate_project_markdown_output, list_available_output_types
        from app.research_projects import add_source_to_project, create_project

        project = create_project(name="Scoped Output Research", pack_id="gaa_vertical")
        add_source_to_project(project["project_id"], first_source, role="core_paper")
        output_types = list_available_output_types(project["project_id"])
        self.assertIn("low_difficulty_first_step_proposal", {item["output_type"] for item in output_types})

        output = generate_project_markdown_output(
            project_id=project["project_id"],
            output_type="low_difficulty_first_step_proposal",
            question="alpha-route baseline validation",
            retrieval_mode="strict_exhaustive",
            llm_backend="none",
        )
        self.assertTrue(Path(output["file_path"]).exists())
        self.assertEqual(output["project_id"], project["project_id"])
        self.assertEqual({item["source_id"] for item in output["citations"]}, {first_source})


if __name__ == "__main__":
    unittest.main()
