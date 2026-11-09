"""Print third-party runtime dependencies of the FamGuard packages (one per line).

Lets the Docker build install dependencies in a cached layer before copying source code.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

PROJECTS = ("backend-api", "worker")
LOCAL_PACKAGES = ("famguard-",)


def main(root: Path) -> int:
    seen: dict[str, None] = {}
    for project in PROJECTS:
        data = tomllib.loads((root / project / "pyproject.toml").read_text(encoding="utf-8"))
        for dep in data["project"]["dependencies"]:
            if not dep.startswith(LOCAL_PACKAGES):
                seen.setdefault(dep, None)
    sys.stdout.write("\n".join(seen) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else ".")))
