"""Package the exact research source that produced the S1 results.

S1 is closed.  The research scripts that produced its results are uncommitted
working-tree files, so S1 reproducibility currently depends on that working tree
never changing.  This copies the exact executed source into the S1 evidence
package with a SHA256 manifest, and cross-verifies each hash against the
per-file hashes that run 1's own source_tree_manifest.json recorded at execution
time.

Deliberately does NOT modify, lint-fix, or reformat any packaged file: the
evidence package must hold the source that actually ran, warts included.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

BASE_GIT_COMMIT = "d2e00e2021ebd02d9966efa0cae2a10e63685fa2"

# (relative source path, phase it was executed for)
EXECUTED_SOURCES: tuple[tuple[str, str], ...] = (
    ("scripts/run_proposal_research_replay.py", "S1A_PART_A_PROPOSAL_BASELINE_REPLAY"),
    ("scripts/s1a_acceptance_readonly.py", "S1A_PART_A_ACCEPTANCE_CHECKS"),
    ("scripts/s1a_extract_findings_readonly.py", "S1A_PART_A_METRIC_EXTRACTION"),
    ("scripts/s1a_gross_vs_net_readonly.py", "S1A_PART_A_GROSS_VS_NET_DECOMPOSITION"),
    ("scripts/s1a_reproducibility_check_readonly.py", "S1A_PART_A_REPRODUCIBILITY_CHECK"),
    ("scripts/s1b_technical_baseline_readonly.py", "S1B_1_PART_B_TECHNICAL_BASELINE"),
    ("scripts/s1b2_v1_calibration_control.py", "C2_PART_C_V1_REPLAY_CALIBRATION"),
    ("scripts/s1b2_fill_semantics_ablation.py", "C2_PART_C_FILL_SEMANTICS_ABLATION"),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_status(repo: Path, relative: str) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", relative],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    line = result.stdout.strip()
    if not line:
        return "COMMITTED_AND_CLEAN"
    code = line[:2].strip()
    return {"M": "UNCOMMITTED_MODIFICATION", "??": "UNTRACKED_NEW_FILE"}.get(code, f"OTHER({code})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--evidence", type=Path, required=True, help="S1 evidence package directory")
    parser.add_argument(
        "--source-tree-manifest",
        type=Path,
        required=True,
        help="run 1 source_tree_manifest.json, used to cross-verify hashes against execution time",
    )
    parser.add_argument(
        "--secondary-source-tree-manifest",
        type=Path,
        default=None,
        help="run 2 source_tree_manifest.json; covers analysis scripts written after run 1 hashed the tree",
    )
    args = parser.parse_args()

    def _recorded_hashes(path: Path) -> dict[str, str]:
        return {item["path"]: item["sha256"] for item in json.loads(path.read_text(encoding="utf-8"))["files"]}

    recorded = _recorded_hashes(args.source_tree_manifest)
    secondary = _recorded_hashes(args.secondary_source_tree_manifest) if args.secondary_source_tree_manifest else {}
    destination = args.evidence / "executed-source"
    destination.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, str | int]] = []
    mismatches: list[str] = []
    for relative, phase in EXECUTED_SOURCES:
        source = args.repo / relative
        if not source.is_file():
            raise FileNotFoundError(source)
        digest = _sha256(source)
        expected = recorded.get(relative)
        expected_secondary = secondary.get(relative)
        if expected is not None:
            if expected == digest:
                verification = "MATCHES_RUN1_EXECUTION_TIME_HASH"
            else:
                verification = "MISMATCH_VS_RUN1_EXECUTION_TIME_HASH"
                mismatches.append(f"{relative}: run1_manifest={expected} now={digest}")
        elif expected_secondary is not None:
            if expected_secondary == digest:
                verification = "MATCHES_RUN2_EXECUTION_TIME_HASH"
            else:
                verification = "MISMATCH_VS_RUN2_EXECUTION_TIME_HASH"
                mismatches.append(f"{relative}: run2_manifest={expected_secondary} now={digest}")
        else:
            verification = "NOT_IN_EITHER_MANIFEST_WRITTEN_AFTER_BOTH_RUNS"
        target = destination / source.name
        shutil.copy2(source, target)
        if _sha256(target) != digest:
            raise RuntimeError(f"copy corrupted: {target}")
        entries.append(
            {
                "file": source.name,
                "source_original_path": relative,
                "executed_for_phase": phase,
                "sha256": digest,
                "size_bytes": source.stat().st_size,
                "git_status": _git_status(args.repo, relative),
                "execution_time_hash_verification": verification,
            }
        )

    manifest = {
        "schema_version": 1,
        "artifact_type": "s1_executed_research_source",
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": BASE_GIT_COMMIT,
        "working_tree_modified": True,
        "runner_commit_status": "UNCOMMITTED_RESEARCH_SOURCE",
        "runner_lint_status": "RUFF_B023_X2",
        "runner_mypy_status": "PASS",
        "semantic_equivalence_of_oom_fix": "PASS",
        "used_for_s1a": True,
        "production_code_modified": False,
        "proposal_replay_service_modified": False,
        "cleanup_policy": {
            "b023_fixed_here": False,
            "reason": "S1 is closed; the evidence package must hold the source that actually produced "
            "the S1 results, not a later tidied version that never ran them.",
            "future_work": "RESEARCH_RUNNER_CLEANUP produces a separate post-S1 runner; it must not be "
            "presented as the S1 runner.",
        },
        "hash_cross_verification": {
            "against": "per-file hashes recorded inside the runs' own source_tree_manifest.json at execution time",
            "run1_source_tree_hash": json.loads(args.source_tree_manifest.read_text(encoding="utf-8"))[
                "source_tree_hash"
            ],
            "run2_source_tree_hash": (
                json.loads(args.secondary_source_tree_manifest.read_text(encoding="utf-8"))["source_tree_hash"]
                if args.secondary_source_tree_manifest
                else None
            ),
            "note": "scripts written after a run hashed the tree cannot appear in that run's manifest; "
            "s1a_reproducibility_check_readonly.py was written after both runs finished and so is "
            "verified only by its own SHA256 recorded here.",
            "mismatches": mismatches,
            "all_verified": not mismatches,
        },
        "files": entries,
    }
    (args.evidence / "EXECUTED_SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print("=== S1 EXECUTED SOURCE PACKAGE ===")
    print(f"destination={destination}")
    for entry in entries:
        print(
            f"  {entry['file']!s:<44} {str(entry['sha256'])[:16]}... {entry['size_bytes']:>7}B  "
            f"{entry['git_status']!s:<24} {entry['execution_time_hash_verification']}"
        )
    print(f"files_packaged={len(entries)}")
    print(f"hash_cross_verification_all_verified={not mismatches}")
    for item in mismatches:
        print(f"  MISMATCH {item}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
