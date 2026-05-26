#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.output_studio import check_local_llm  # noqa: E402


def main() -> int:
    status = check_local_llm()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status.get("status") == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
