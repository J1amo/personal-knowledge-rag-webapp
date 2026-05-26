#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

preferred_python = os.getenv("PKB_PYTHON") or os.getenv("PYTHON")
if preferred_python:
    preferred_path = Path(preferred_python).expanduser()
    if preferred_path.exists() and Path(sys.executable).resolve() != preferred_path.resolve():
        os.execv(str(preferred_path), [str(preferred_path), *sys.argv])

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.doi_downloader import doi_downloader_status  # noqa: E402


def main() -> int:
    status = doi_downloader_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status.get("playwright_installed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
