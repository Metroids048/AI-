"""Enforce the unified automated-trading freeze without a persistent env bypass."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "contracts" / "automated_trading_frozen_contract.json"
ACCEPTANCE_PATH = REPO_ROOT / "contracts" / "business_acceptance_state.json"
V2_CONTRACT_PATH = REPO_ROOT / "contracts" / "v2_transaction_contract.json"
FAILURE_CODE = "AUTO_TRADING_FROZEN_CONTRACT_VIOLATION"
STALE_APPROVAL_CODE = "AUTO_TRADING_STALE_ENV_APPROVAL_REJECTED"
LEDGER_FAILURE_CODE = "AUTO_TRADING_ACCEPTANCE_LEDGER_VIOLATION"
FINAL_CLOSEOUT_FAILURE_CODE = "AUTO_TRADING_FINAL_CLOSEOUT_BLOCKED"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def load_acceptance_state() -> dict[str, Any]:
    return json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8"))


def load_v2_contract() -> dict[str, Any]:
    return json.loads(V2_CONTRACT_PATH.read_text(encoding="utf-8"))


def acceptance_ledger_errors(
    contract: dict[str, Any],
    acceptance: dict[str, Any] | None = None,
    transaction: dict[str, Any] | None = None,
    *,
    require_natural: bool = False,
) -> list[str]:
    """Validate the cross-ledger SHA and stage gate before freeze/closeout."""
    acceptance = acceptance or load_acceptance_state()
    transaction = transaction or load_v2_contract()
    errors: list[str] = []
    frozen_sha = str(contract.get("baseline_sha") or "")
    accepted_sha = str(acceptance.get("validated_code_sha") or "")
    transaction_sha = str(transaction.get("baseline_sha") or "")
    for label, value in (
        ("frozen baseline_sha", frozen_sha),
        ("business validated_code_sha", accepted_sha),
        ("v2 transaction baseline_sha", transaction_sha),
    ):
        if not _COMMIT_SHA.fullmatch(value):
            errors.append(f"{label} must be a full 40-character commit SHA")
    if _COMMIT_SHA.fullmatch(frozen_sha) and _COMMIT_SHA.fullmatch(accepted_sha) and frozen_sha != accepted_sha:
        errors.append(f"business validated_code_sha {accepted_sha} != frozen baseline_sha {frozen_sha}")
    if _COMMIT_SHA.fullmatch(frozen_sha) and _COMMIT_SHA.fullmatch(transaction_sha) and frozen_sha != transaction_sha:
        errors.append(f"v2 transaction baseline_sha {transaction_sha} != frozen baseline_sha {frozen_sha}")
    if acceptance.get("runtime_readiness") != "PASS":
        errors.append("runtime_readiness must be PASS for engineering freeze")
    if acceptance.get("runtime_recovery_resilience") != "PASS":
        errors.append("runtime_recovery_resilience must be PASS for engineering freeze")
    if require_natural and acceptance.get("natural_testnet_execution") != "PASS":
        errors.append("natural_testnet_execution must be PASS for final closeout")
    return errors


def protected_paths(contract: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, str] = {}
    for key in ("transaction_protected_paths", "runtime_protected_paths"):
        values = contract.get(key)
        if not isinstance(values, dict):
            raise ValueError(f"{key} must be an object")
        for path, digest in values.items():
            if path in paths and paths[path] != digest:
                raise ValueError(f"conflicting frozen hash for {path}")
            paths[path] = str(digest)
    return paths


def staged_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def protected_staged_paths(staged: list[str], frozen: dict[str, str]) -> list[str]:
    return sorted(path for path in staged if path in frozen)


def _hash_at_revision(revision: str, path: str) -> str | None:
    completed = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return hashlib.sha256(completed.stdout).hexdigest()


def baseline_hashes_match(contract: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    baseline_sha = str(contract["baseline_sha"])
    for path, expected in protected_paths(contract).items():
        actual = _hash_at_revision(baseline_sha, path)
        if actual is None:
            failures.append(f"{path}: missing from baseline {baseline_sha}")
        elif actual != expected:
            failures.append(f"{path}: expected {expected}, got {actual}")
    return failures


def current_head_hashes_match(contract: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for path, expected in protected_paths(contract).items():
        actual = _hash_at_revision("HEAD", path)
        if actual is None:
            failures.append(f"{path}: missing from current HEAD")
        elif actual != expected:
            failures.append(f"{path}: frozen {expected}, current HEAD {actual}")
    return failures


def _load_approval(contract: dict[str, Any]) -> dict[str, Any] | None:
    approval = contract.get("approval")
    if not isinstance(approval, dict):
        raise ValueError("approval must be an object")
    path = REPO_ROOT / str(approval["local_approval_path"])
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def approval_errors(contract: dict[str, Any], touched: list[str]) -> list[str]:
    approval = _load_approval(contract)
    if approval is None:
        return ["no one-time local approval file is present"]
    required = contract["approval"]["required_fields"]
    if not isinstance(required, list) or any(not approval.get(field) for field in required):
        return ["approval is missing a required non-empty field"]
    if approval.get("base_sha") != contract["baseline_sha"]:
        return ["approval base_sha does not match frozen baseline"]
    allowed_paths = approval.get("allowed_paths")
    if not isinstance(allowed_paths, list) or any(path not in protected_paths(contract) for path in allowed_paths):
        return ["approval allowed_paths must be an explicit subset of frozen paths"]
    if not set(touched).issubset(set(allowed_paths)):
        return ["approval does not cover every changed frozen path"]
    required_acceptance = set(contract["required_acceptance"])
    supplied_acceptance = approval.get("required_acceptance")
    if not isinstance(supplied_acceptance, list) or not required_acceptance.issubset(supplied_acceptance):
        return ["approval does not bind the complete required acceptance set"]
    return []


def _reject_stale_environment_approval() -> bool:
    return bool(os.environ.get("V2_HOTPATH_FIX_APPROVED") or os.environ.get("V2_HOTPATH_FIX_ID"))


def _print_failures(failures: list[str]) -> None:
    print(FAILURE_CODE, file=sys.stderr)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="check staged paths before commit")
    parser.add_argument("--verify-baseline", action="store_true", help="verify hashes stored in the contract")
    parser.add_argument("--verify-head", action="store_true", help="verify protected paths in current HEAD")
    parser.add_argument(
        "--verify-ledger", action="store_true", help="verify acceptance/freeze SHA and engineering stage"
    )
    parser.add_argument(
        "--verify-final-closeout",
        action="store_true",
        help="require matching ledgers and natural Testnet PASS",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable result after the requested frozen-contract checks",
    )
    args = parser.parse_args()
    contract = load_contract()

    if _reject_stale_environment_approval():
        print(STALE_APPROVAL_CODE, file=sys.stderr)
        print("Use the bounded local approval record; V2_HOTPATH_FIX_* is not accepted.", file=sys.stderr)
        return 1

    if args.verify_baseline:
        failures = baseline_hashes_match(contract)
        if failures:
            _print_failures(failures)
            return 1
        print(f"PASS: automated trading baseline {contract['baseline_sha']} hashes match")

    if args.verify_head:
        failures = current_head_hashes_match(contract)
        if failures:
            approval_failures = approval_errors(contract, [failure.split(":", 1)[0] for failure in failures])
            if approval_failures:
                _print_failures(failures + approval_failures)
                return 1
            print("PASS: bounded one-time approval covers current frozen-path drift")
        else:
            print("PASS: current HEAD protected paths match automated trading baseline")

    if args.staged:
        frozen = protected_paths(contract)
        touched = protected_staged_paths(staged_paths(), frozen)
        if touched:
            failures = approval_errors(contract, touched)
            if failures:
                _print_failures([f"protected paths staged: {', '.join(touched)}"] + failures)
                return 1
            print(f"PASS: bounded one-time approval covers {len(touched)} frozen path(s)")
        else:
            print("PASS: no automated-trading frozen paths staged")
    if args.verify_ledger or args.verify_final_closeout:
        ledger_failures = acceptance_ledger_errors(
            contract,
            require_natural=args.verify_final_closeout,
        )
        if ledger_failures:
            print(
                FINAL_CLOSEOUT_FAILURE_CODE if args.verify_final_closeout else LEDGER_FAILURE_CODE,
                file=sys.stderr,
            )
            for failure in ledger_failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            return 1
        if args.verify_final_closeout:
            print("FINAL_CLOSEOUT=PASS")
        else:
            natural = load_acceptance_state().get("natural_testnet_execution")
            print(f"ENGINEERING_FREEZE=PASS; NATURAL_TESTNET_EXECUTION={natural}")
    if args.json:
        print(
            json.dumps(
                {
                    "contract": "automated_trading_frozen_contract",
                    "status": "PASS",
                    "checks_requested": {
                        "staged": args.staged,
                        "verify_baseline": args.verify_baseline,
                        "verify_head": args.verify_head,
                    },
                    "execution_contract_health": "PASS",
                    "natural_strategy_business_recovery": "NOT_EVALUATED",
                    "natural_auto_trading_recovery": "NOT_EVALUATED",
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
