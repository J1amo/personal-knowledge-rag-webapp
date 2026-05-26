from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
