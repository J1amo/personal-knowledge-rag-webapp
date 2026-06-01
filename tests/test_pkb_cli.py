from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


class PkbCliTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def env_for(self, data_root: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PKB_DATA_DIR": str(data_root / "data"),
                "PKB_DB_DIR": str(data_root / "db"),
                "PKB_INDEX_DIR": str(data_root / "indexes"),
                "PKB_CACHE_DIR": str(data_root / "cache"),
                "PKB_BACKUP_DIR": str(data_root / "backups"),
                "PKB_OUTPUT_DIR": str(data_root / "outputs"),
                "LOCAL_MODELS_DIR": str(data_root / "local_models"),
                "PYTHONPYCACHEPREFIX": "/tmp/pkb-pycache",
            }
        )
        return env

    def run_pkb(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.root / "scripts" / "pkb.sh"), *args],
            cwd=self.root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_workflow_lists_short_paths(self) -> None:
        result = self.run_pkb("workflow")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("./scripts/pkb.sh ingest", result.stdout)
        self.assertIn("./scripts/pkb.sh chatgpt-packet", result.stdout)
        self.assertIn("./scripts/pkb.sh codex", result.stdout)
        self.assertIn("隐私边界", result.stdout)

    def test_doctor_reports_json_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_pkb("doctor", "--json", env=self.env_for(Path(tmp)))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["system_health"], "ok")
        self.assertIn("database", payload)
        self.assertEqual(payload["database"]["sources"], 0)
        self.assertIn("pymupdf_available", payload)
        self.assertIn("pytest_available", payload)

    def test_codex_generates_repair_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            result = self.run_pkb(
                "codex",
                "--reason",
                "smoke handoff",
                "--json",
                env=self.env_for(data_root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            path = Path(payload["file_path"])
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("smoke handoff", content)
            self.assertIn("Do not delete raw files automatically", content)

    def test_chatgpt_packet_generates_zip_without_raw_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            env = self.env_for(data_root)
            init = self.run_pkb("doctor", "--json", env=env)
            self.assertEqual(init.returncode, 0, init.stderr)

            db_path = data_root / "db" / "knowledge.sqlite"
            now = "2026-01-01T00:00:00+00:00"
            with sqlite3.connect(db_path) as con:
                con.execute("PRAGMA foreign_keys = ON")
                con.executemany(
                    """
                    INSERT INTO sources (
                      source_id, original_filename, raw_path, file_hash, file_size,
                      source_type, domain, topic, modality, sensitivity, created_at,
                      ingested_at, parser_version, ingestion_status
                    )
                    VALUES (?, ?, ?, ?, ?, 'pdf', ?, ?, 'text', ?, ?, ?, 'test_parser', 'ready')
                    """,
                    [
                        (
                            "src_public_1",
                            "paper_one.pdf",
                            "/private/raw/paper_one.pdf",
                            "hash_public_1",
                            123,
                            "paper",
                            "vertical transistors",
                            "public",
                            now,
                            now,
                        ),
                        (
                            "src_public_2",
                            "paper_two.pdf",
                            "/private/raw/paper_two.pdf",
                            "hash_public_2",
                            456,
                            "paper",
                            "research design",
                            "public",
                            now,
                            now,
                        ),
                        (
                            "src_private_1",
                            "private_note.pdf",
                            "/private/raw/private_note.pdf",
                            "hash_private_1",
                            789,
                            "misc",
                            "secret",
                            "confidential",
                            now,
                            now,
                        ),
                    ],
                )
                con.executemany(
                    """
                    INSERT INTO chunks (
                      chunk_id, source_id, chunk_index, text, text_hash, page_number,
                      section_title, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "chk_public_1",
                            "src_public_1",
                            0,
                            "Vertical transistor research scheme uses evidence gap analysis and method comparison.",
                            "text_hash_1",
                            1,
                            "Abstract",
                            now,
                        ),
                        (
                            "chk_public_2",
                            "src_public_2",
                            0,
                            "A research plan should compare data needs, risks, methods, and evidence gaps.",
                            "text_hash_2",
                            2,
                            "Methods",
                            now,
                        ),
                        (
                            "chk_private_1",
                            "src_private_1",
                            0,
                            "Confidential personal note that must not enter a public paper packet.",
                            "text_hash_3",
                            1,
                            "Private",
                            now,
                        ),
                    ],
                )

            result = self.run_pkb(
                "chatgpt-packet",
                "GPT-5.5 research scheme evidence gap method vertical transistor",
                "--retrieval-mode",
                "strict_exhaustive",
                "--top-k",
                "4",
                "--output-dir",
                str(data_root / "packet-output"),
                "--no-copy",
                "--json",
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["paper_count"], 2)
            self.assertFalse(payload["raw_pdfs_included"])
            self.assertEqual(payload["clipboard"]["status"], "skipped")
            zip_path = Path(payload["zip_path"])
            prompt_path = Path(payload["prompt_path"])
            self.assertTrue(zip_path.exists())
            self.assertIn("GPT-5.5", zip_path.name)
            self.assertTrue(prompt_path.exists())
            self.assertIn("原始 PDF 不在 zip 中", prompt_path.read_text(encoding="utf-8"))
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith("paper_manifest.csv") for name in names))
            self.assertTrue(any(name.endswith("chatgpt_prompt.md") for name in names))
            self.assertTrue(any(name.endswith("01_source_grounded_report.md") for name in names))
            self.assertFalse(any(name.lower().endswith(".pdf") for name in names))


if __name__ == "__main__":
    unittest.main()
