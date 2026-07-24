"""Deterministic verification for the BTC/ETH exchange-first directional path.

This command never creates a real exchange gateway and never performs network
I/O. It runs the production decision/orchestration path against a strict fake
Binance adapter, then writes a machine-readable proof artifact.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = PROJECT_ROOT / "artifacts" / "directional-exchange-first-verification.json"
TESTS = [
    "tests/services/test_directional_sampling_fallback.py",
    (
        "tests/services/test_paper_runtime.py"
        "::test_directional_sampling_fallback_reaches_exchange_fill_and_local_projection"
    ),
    (
        "tests/services/test_paper_bootstrap.py"
        "::test_bootstrap_stages_manifest_rules_when_active_runtime_snapshot_is_stale"
    ),
]


def main() -> int:
    command = [sys.executable, "-m", "pytest", "-q", *TESTS]
    completed = subprocess.run(  # noqa: S603 - fixed local command, no user input
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = {
        "verified_at": datetime.now(UTC).isoformat(),
        "status": "passed" if completed.returncode == 0 else "failed",
        "scope": "BTC/ETH exchange-first directional production path with strict fake Binance adapter",
        "network_calls": 0,
        "real_exchange_orders": 0,
        "command": command,
        "tests": TESTS,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "proof_contract": [
            "real indicator fallback emits a directional candidate when 15m agrees with one higher timeframe",
            "candidate reaches Gatekeeper and market-rule normalization",
            "gateway create-order boundary receives one order",
            "confirmed exchange fill price and quantity create the local projection",
            "stale runtime strategy rules are staged from the active manifest",
        ],
        "limitations": [
            (
                "This is deterministic offline verification and cannot prove connectivity "
                "to the operator's Binance Demo account."
            ),
            (
                "A real Binance Demo order ID can only be produced on the operator device "
                "with its credentials and network."
            ),
        ],
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    print(f"verification_artifact={ARTIFACT}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
