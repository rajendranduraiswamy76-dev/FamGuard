"""Apply the FamGuard Postgres schema. Run from the repo root:

    python infrastructure/scripts/migrate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend-api"))

from app.database import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--migrate"]))
