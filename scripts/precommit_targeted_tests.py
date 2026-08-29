"""Compatibility entry point for the deterministic frozen-contract gate.

The implementation lives in ``precommit_fast_contract_gate.ps1`` so the
supervising process can remain independent from the Python/pytest child tree on
Windows. Keeping this filename avoids silently reviving the former fuzzy
stem-to-``pytest -k`` selector for callers that still invoke it directly.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    powershell = shutil.which("pwsh")
    if powershell is None:
        print("[pre-commit] FAIL: pwsh is required for the frozen fast contract gate.", file=sys.stderr)
        return 1
    script = Path(__file__).with_name("precommit_fast_contract_gate.ps1")
    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *sys.argv[1:]],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
