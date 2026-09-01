from __future__ import annotations

from pathlib import Path

from scripts.verify_automated_trading_contract import (
    CONTRACT_PATH,
    baseline_hashes_match,
    current_head_hashes_match,
    load_contract,
    protected_paths,
    protected_staged_paths,
)


def test_unified_contract_is_machine_readable_and_matches_its_baseline() -> None:
    contract = load_contract()

    assert CONTRACT_PATH.exists()
    assert contract["contract_version"].startswith("automated-trading-freeze-")
    assert contract["canonical_owner"] == "contracts/automated_trading_frozen_contract.json"
    assert baseline_hashes_match(contract) == []
    assert current_head_hashes_match(contract) == []


def test_runtime_continuity_paths_are_frozen_with_transaction_semantics() -> None:
    contract = load_contract()
    runtime_paths = set(contract["runtime_protected_paths"])

    assert {
        "一键启动.cmd",
        "scripts/launch-paper-console.ps1",
        "scripts/run-local-paper-scheduler.py",
        "services/execution/runtime_state.py",
        "services/execution/natural_testnet_mode.py",
        "services/execution/scheduler.py",
        "services/execution/v2_scheduler_entry.py",
    }.issubset(runtime_paths)
    assert len(protected_paths(contract)) >= 16


def test_behavioral_and_fault_regression_suites_are_part_of_the_contract() -> None:
    contract = load_contract()

    assert len(contract["runtime_invariants"]) >= 10
    assert (
        "test_supervisor_keeps_running_after_ten_recoverable_external_failures"
        in contract["required_fault_injection_tests"]
    )
    assert "binance_testnet_acceptance_lifecycle" in contract["required_e2e_checks"]


def test_fast_contract_gate_uses_exact_hermetic_nodeids() -> None:
    contract = load_contract()
    nodeids = contract["fast_contract_tests"]
    gate_script = Path("scripts/precommit_fast_contract_gate.ps1").read_text(encoding="utf-8")

    assert isinstance(contract["fast_contract_timeout_seconds"], int)
    assert contract["fast_contract_timeout_seconds"] > 0
    assert len(nodeids) >= 6
    assert len(nodeids) == len(set(nodeids))
    assert all("::" in nodeid for nodeid in nodeids)
    assert "fast_contract_tests" in gate_script
    assert "FAIL_HUNG_CONTRACT_TEST" in gate_script
    assert "taskkill.exe" in gate_script
    assert '"-k"' not in gate_script


def test_unrelated_paths_do_not_trigger_the_automated_trading_freeze() -> None:
    contract = load_contract()

    assert (
        protected_staged_paths(
            [
                "research_source/video_strategy.py",
                "frontend/admin/src/pages/PaperConsole.jsx",
                "services/strategy_library/metadata.py",
            ],
            protected_paths(contract),
        )
        == []
    )
