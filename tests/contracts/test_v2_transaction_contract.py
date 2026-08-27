from __future__ import annotations

import json

from scripts.verify_v2_transaction_contract import (
    CONTRACT_PATH,
    FAILURE_CODE,
    baseline_hashes_match,
    current_head_hashes_match,
    load_contract,
    protected_staged_paths,
)


def test_v2_transaction_contract_is_machine_readable_and_matches_baseline() -> None:
    contract = load_contract()

    assert CONTRACT_PATH.exists()
    assert contract["contract_version"].startswith("v2-transaction-freeze-")
    assert contract["baseline_sha"] == "68b73db26c1418294736cb3d2571da80b9107901"
    assert len(contract["protected_paths"]) >= 10
    assert baseline_hashes_match(contract) == []


def test_only_explicitly_protected_paths_are_blocked() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert protected_staged_paths(
        [
            "services/execution/scheduler.py",
            "apps/api/routers/runtime.py",
            "frontend/admin/src/pages/PaperConsole.jsx",
        ],
        contract["protected_paths"],
    ) == ["services/execution/scheduler.py"]
    assert FAILURE_CODE == "V2_HOTPATH_CHANGE_REQUIRES_EXPLICIT_APPROVAL"


def test_current_head_drift_is_visible_for_protected_paths(monkeypatch) -> None:
    contract = load_contract()

    def fake_run(command, **kwargs):  # noqa: ANN001
        class Result:
            returncode = 0
            stdout = b"drifted"

        return Result()

    monkeypatch.setattr("scripts.verify_v2_transaction_contract.subprocess.run", fake_run)
    failures = current_head_hashes_match(contract)

    assert failures
    assert any("current HEAD" in item for item in failures)
