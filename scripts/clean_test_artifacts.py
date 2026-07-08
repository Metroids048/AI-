"""Remove stale pytest SQLite artifacts from .local/test-runtime/."""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def clean_test_artifacts(*, project_root: Path, max_age_days: float) -> int:
    target_dir = project_root / ".local" / "test-runtime"
    if not target_dir.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for path in target_dir.glob("pytest_ai_quant*.db"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-days", type=float, default=7.0)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    removed = clean_test_artifacts(project_root=args.project_root, max_age_days=args.max_age_days)
    print(f"removed {removed} stale test db file(s)")


if __name__ == "__main__":
    main()
