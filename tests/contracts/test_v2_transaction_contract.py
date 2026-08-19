from __future__ import annotations

import json

from scripts.verify_v2_transaction_contract import (
    CONTRACT_PATH,
    FAILURE_CODE,
    baseline_hashes_match,
    load_contract,
    protected_staged_paths,
)


def test_v2_transaction_contract_is_machine_readable_and_matches_baseline() -> None:
    contract = load_contract()

    assert CONTRACT_PATH.exists()
    assert contract["contract_version"].startswith("v2-transaction-freeze-")
    assert contract["baseline_sha"] == "df10d57d47228d673b8cf6c627819ce6633e95af"
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
