from __future__ import annotations

import json
from pathlib import Path

from app.api.main import create_app


def test_committed_openapi_matches_application_schema() -> None:
    spec_path = Path(__file__).resolve().parents[1] / "openapi.json"
    committed_spec = json.loads(spec_path.read_text(encoding="utf-8"))

    assert committed_spec == create_app().openapi()
    assert "/internal/cron/expiry-sweep" not in committed_spec["paths"]
