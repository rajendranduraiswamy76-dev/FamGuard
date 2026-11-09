from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.update(
    {
        "ENV": "test",
        "DATABASE_URL": "postgresql://test:test@localhost:5432/famguard_test",
        "OPENAI_API_KEY": "test",
        "PINECONE_API_KEY": "test",
    }
)

from app.api.main import app  # noqa: E402


def main() -> None:
    output = ROOT / "openapi.json"
    output.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
