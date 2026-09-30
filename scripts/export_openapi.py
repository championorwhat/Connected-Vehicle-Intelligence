"""Write the API's OpenAPI document to docs/api/openapi.json (checked in; a test keeps it current).

uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from prognos_api.config import Settings
from prognos_api.main import create_app

OUT = Path(__file__).resolve().parents[1] / "docs/api/openapi.json"


def generate() -> str:
    return json.dumps(create_app(Settings()).openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(generate())
    print(f"wrote {OUT}")
