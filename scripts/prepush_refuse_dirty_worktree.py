"""Refuse git push when the working tree still has uncommitted changes.

Prevents: pre-commit autofix aborts commit -> operator runs ``git push``
anyway -> GitHub stays on the old HEAD while local edits look finished.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    dirty = result.stdout.strip()
    if not dirty:
        return 0

    print(
        "ERROR: refusing push — working tree has uncommitted changes.\n"
        "Commit (or stash) first. If a pre-commit hook just fixed files, run:\n"
        "  git add -A && git commit && git push\n",
        file=sys.stderr,
    )
    print(dirty, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
