from __future__ import annotations

import os
import plistlib
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

    def test_generated_launchagent_plist_pins_project_python(self) -> None:
        pinned_python = "/example/project/python3"
        env = dict(os.environ)
        env["PKB_PYTHON"] = pinned_python
        result = subprocess.run(
            [str(self.root / "scripts" / "webapp.sh"), "plist"],
            cwd=self.root,
            env=env,
            text=False,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        plist = plistlib.loads(result.stdout)
        self.assertEqual(plist["EnvironmentVariables"]["PKB_PYTHON"], pinned_python)
        self.assertEqual(plist["ProgramArguments"], [str(self.root / "scripts" / "run_server.sh")])


if __name__ == "__main__":
    unittest.main()
