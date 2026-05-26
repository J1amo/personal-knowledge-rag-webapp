from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


class LaunchdRuntimeTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_system_python_can_import_server_entrypoint(self) -> None:
        system_python = Path("/usr/bin/python3")
        if not system_python.exists():
            self.skipTest("/usr/bin/python3 is not available")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.root)
        result = subprocess.run(
            [str(system_python), "-c", "import app.server; print('ok')"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
