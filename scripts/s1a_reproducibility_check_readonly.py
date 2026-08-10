"""Read-only reproducibility check between two independent S1A runs.

Run 1 (22:16) was snapshotted before run 2 (22:28) overwrote the canonical
directory.  Both executed byte-identical replay code over the same data_hash, so
this compares them to establish whether S1A is deterministic.  Reads only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

VOLATILE_MANIFEST_KEYS = {"generated_at"}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _diff_paths(left: Any, right: Any, trail: str = "") -> list[str]:
    if type(left) is not type(right):
        return [f"{trail or '<root>'}: type {type(left).__name__} vs {type(right).__name__}"]
    if isinstance(left, dict):
        found: list[str] = []
        for key in sorted(set(left) | set(right)):
            here = f"{trail}.{key}" if trail else str(key)
            if key not in left:
                found.append(f"{here}: missing in left")
            elif key not in right:
                found.append(f"{here}: missing in right")
            else:
                found.extend(_diff_paths(left[key], right[key], here))
        return found
    if isinstance(left, list):
        if len(left) != len(right):
            return [f"{trail}: length {len(left)} vs {len(right)}"]
        found = []
        for index, (lhs, rhs) in enumerate(zip(left, right, strict=True)):
            found.extend(_diff_paths(lhs, rhs, f"{trail}[{index}]"))
        return found
    if left != right:
        return [f"{trail}: {left!r} vs {right!r}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run1", type=Path, required=True, help="clean snapshot of the first run")
    parser.add_argument("--run2", type=Path, required=True, help="directory overwritten by the second run")
    args = parser.parse_args()

    print("=== S1A REPRODUCIBILITY CHECK (read-only) ===")
    print(f"run1={args.run1}")
    print(f"run2={args.run2}")
    print()

    report_diffs = _diff_paths(
        _load(args.run1 / "proposal-research-report.json"),
        _load(args.run2 / "proposal-research-report.json"),
    )
    print(
        f"[{'PASS' if not report_diffs else 'FAIL'}] proposal-research-report.json identical: diffs={len(report_diffs)}"
    )
    for item in report_diffs[:10]:
        print(f"    {item}")

    left_manifest = _load(args.run1 / "PHASE1_MANIFEST.json")
    right_manifest = _load(args.run2 / "PHASE1_MANIFEST.json")
    manifest_diffs = [
        item
        for item in _diff_paths(left_manifest, right_manifest)
        if not any(item.startswith(key) for key in VOLATILE_MANIFEST_KEYS)
    ]
    print(
        f"[{'PASS' if not manifest_diffs else 'FAIL'}] PHASE1_MANIFEST.json identical "
        f"(ignoring {sorted(VOLATILE_MANIFEST_KEYS)}): diffs={len(manifest_diffs)}"
    )
    for item in manifest_diffs[:10]:
        print(f"    {item}")
    for key in ("data_hash", "config_hash", "source_tree_hash"):
        same = left_manifest[key] == right_manifest[key]
        print(
            f"    {key}: {'SAME' if same else 'DIFFERENT'}  run1={left_manifest[key][:16]}... "
            f"run2={right_manifest[key][:16]}..."
        )

    config_diffs = _diff_paths(_load(args.run1 / "config_manifest.json"), _load(args.run2 / "config_manifest.json"))
    print(f"[{'PASS' if not config_diffs else 'FAIL'}] config_manifest.json identical: diffs={len(config_diffs)}")

    left_ledger = [
        json.loads(line) for line in (args.run1 / "trial-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line
    ]
    right_lines = [line for line in (args.run2 / "trial-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
    right_ledger = [json.loads(line) for line in right_lines]
    print()
    print(f"run1 ledger records={len(left_ledger)}  run2 ledger records={len(right_ledger)}")
    if len(right_ledger) == 2 * len(left_ledger):
        first_half = right_ledger[: len(left_ledger)]
        second_half = right_ledger[len(left_ledger) :]
        halves_diffs = _diff_paths(first_half, second_half)
        print(
            f"[{'PASS' if not halves_diffs else 'FAIL'}] run2 ledger is exactly run1's 48 records appended twice: "
            f"diffs between halves={len(halves_diffs)}"
        )
        against_run1 = _diff_paths(left_ledger, first_half)
        print(f"[{'PASS' if not against_run1 else 'FAIL'}] run2 first half == run1 snapshot: diffs={len(against_run1)}")
        for item in halves_diffs[:6]:
            print(f"    {item}")
    else:
        print("    (unexpected ledger length ratio; skipping half comparison)")

    print()
    print("interpretation: identical report + identical ledger halves means S1A is")
    print("deterministic for fixed code and fixed data_hash, so the concurrent-writer")
    print("incident cost nothing in research terms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
