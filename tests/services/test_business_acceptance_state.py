from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.execution.business_acceptance import BusinessAcceptanceState


def test_business_acceptance_state_keeps_runtime_and_business_stages_distinct() -> None:
    state = BusinessAcceptanceState.runtime_pass(validated_code_sha="a" * 40)
    payload = state.to_dict()
    assert payload["runtime_readiness"] == "PASS"
    assert payload["runtime_recovery_resilience"] == "NOT_VERIFIED"
    assert payload["natural_testnet_execution"] == "BLOCKED"
    assert payload["strategy_business_recovery"] == "NOT_VERIFIED"
    assert payload["profitability_validation"] == "NOT_VERIFIED"


def test_business_acceptance_pass_requires_validated_code_sha() -> None:
    with pytest.raises(ValueError, match="validated_code_sha"):
        BusinessAcceptanceState(validated_code_sha=None, runtime_readiness="PASS")


def test_business_acceptance_sha_must_be_a_full_commit_sha() -> None:
    with pytest.raises(ValueError, match="40-character commit SHA"):
        BusinessAcceptanceState(validated_code_sha="not-a-sha", runtime_readiness="PASS")


def test_business_acceptance_contract_is_machine_readable_and_not_code_pass() -> None:
    path = Path("contracts/business_acceptance_state.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "business-acceptance-state-1"
    assert payload["runtime_readiness"] in {"NOT_VERIFIED", "PASS"}
    if payload["runtime_readiness"] == "PASS":
        assert payload["validated_code_sha"]
    assert payload["natural_testnet_execution"] != "PASS"
    assert payload["profitability_validation"] != "PASS"
