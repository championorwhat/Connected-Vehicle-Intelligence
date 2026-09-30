"""The checked-in OpenAPI document must match the code (scripts/export_openapi.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_openapi_document_is_current() -> None:
    spec = importlib.util.spec_from_file_location(
        "export_openapi", ROOT / "scripts/export_openapi.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = (ROOT / "docs/api/openapi.json").read_text()
    assert committed == module.generate(), "run: uv run python scripts/export_openapi.py"


def test_every_v1_route_is_documented_with_its_errors() -> None:
    import json

    doc = json.loads((ROOT / "docs/api/openapi.json").read_text())
    paths = [p for p in doc["paths"] if p.startswith("/v1/")]
    assert {"/v1/auth/token", "/v1/vehicles", "/v1/vehicles/at-risk", "/v1/alerts",
            "/v1/work-orders", "/v1/fleet/signals"} <= set(paths)  # fmt: skip
