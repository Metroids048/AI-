"""Enforce the machine-readable V2 transaction hot-path freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "contracts" / "v2_transaction_contract.json"
FAILURE_CODE = "V2_HOTPATH_CHANGE_REQUIRES_EXPLICIT_APPROVAL"


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def protected_staged_paths(staged_paths: list[str], protected_paths: dict[str, str]) -> list[str]:
    protected = set(protected_paths)
    return sorted(path for path in staged_paths if path in protected)


def staged_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def baseline_hashes_match(contract: dict) -> list[str]:
    failures: list[str] = []
    baseline_sha = str(contract["baseline_sha"])
    for path, expected in contract["protected_paths"].items():
        completed = subprocess.run(
            ["git", "show", f"{baseline_sha}:{path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            failures.append(f"{path}: missing from baseline {baseline_sha}")
            continue
        actual = hashlib.sha256(completed.stdout).hexdigest()
        if actual != expected:
            failures.append(f"{path}: expected {expected}, got {actual}")
    return failures


def _explicit_hotpath_approval() -> bool:
    return os.environ.get("V2_HOTPATH_FIX_APPROVED") == "1" and bool(os.environ.get("V2_HOTPATH_FIX_ID", "").strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="check staged paths before commit")
    parser.add_argument("--verify-baseline", action="store_true", help="verify hashes stored in the contract")
    args = parser.parse_args()
    contract = load_contract()

    if args.verify_baseline:
        failures = baseline_hashes_match(contract)
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            return 1
        print(f"PASS: V2 transaction baseline {contract['baseline_sha']} hashes match")

    if args.staged:
        touched = protected_staged_paths(staged_paths(), contract["protected_paths"])
        if touched and not _explicit_hotpath_approval():
            print(FAILURE_CODE, file=sys.stderr)
            print("Protected V2 transaction paths were staged:", file=sys.stderr)
            for path in touched:
                print(f"  - {path}", file=sys.stderr)
            print(
                "Dedicated hotpath fixes require V2_HOTPATH_FIX_APPROVED=1 and a non-empty V2_HOTPATH_FIX_ID.",
                file=sys.stderr,
            )
            return 1
        if touched:
            print(
                f"PASS: explicit hotpath approval {os.environ['V2_HOTPATH_FIX_ID'].strip()} "
                f"covers {len(touched)} protected path(s)"
            )
        else:
            print("PASS: no protected V2 transaction paths staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
