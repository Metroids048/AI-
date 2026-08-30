from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from services.automated_trading.application.production_strategy import (
    EntryAuthority,
    resolve_entry_authority,
    resolve_testnet_forward_authorization,
)
from services.strategy_library.candidates.registry import get_candidate
from services.validation.forward_validation import (
    ForwardDensityMetrics,
    ForwardValidationHandoff,
    build_forward_validation_handoff,
    forward_runtime_binding_hash,
)
from services.validation.strategy_promotion import PromotionResult
from shared.models.trading import canonical_config_hash


def _handoff() -> ForwardValidationHandoff:
    candidate = get_candidate("trend_momentum_v2_enriched")
    runtime_config = {
        "execution_profile": {"risk_profile_id": "test-risk-profile", "risk_per_trade": 0.05},
        "strategy_rules": candidate.get_config(),
        "risk_profile_id": "test-risk-profile",
    }
    density = ForwardDensityMetrics(
        eligible_closed_bars=1000,
        candidate_count=80,
        closed_trade_count=80,
        closed_trades_per_day=2.0,
        median_inter_trade_hours=8.0,
        p90_inter_trade_hours=24.0,
        estimated_days_to_30_closed_trades=30.0,
        passed=True,
    )
    return build_forward_validation_handoff(
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        strategy_rules=candidate.get_config(),
        dataset_hash="d" * 64,
        validation_evidence_ref="artifacts/validation/evidence.json",
        validation_evidence_hash="e" * 64,
        eligible_execution_symbols=("BTC/USDT", "ETH/USDT"),
        density=density,
        profitability_recovery=PromotionResult(eligible=True, failed_requirements=()),
        runtime_config=runtime_config,
        frozen_at=datetime(2026, 8, 30, tzinfo=UTC),
    )


def _snapshot_config(handoff: dict) -> dict:
    return {
        "execution_profile": {"risk_profile_id": "test-risk-profile", "risk_per_trade": 0.05},
        "strategy_rules": handoff["strategy_rules"],
        "risk_profile_id": "test-risk-profile",
        "forward_validation": handoff,
    }


def test_forward_handoff_is_sealed_and_deterministically_reloaded() -> None:
    handoff = _handoff()
    payload = handoff.model_dump(mode="json")

    assert ForwardValidationHandoff.model_validate(payload).handoff_hash == handoff.handoff_hash
    with pytest.raises(ValidationError):
        handoff.status = "APPROVED"  # type: ignore[misc]


def test_runtime_binding_excludes_handoff_without_mutating_input() -> None:
    handoff = _handoff().model_dump(mode="json")
    snapshot_config = _snapshot_config(handoff)
    original = deepcopy(snapshot_config)

    binding = forward_runtime_binding_hash(snapshot_config)

    assert binding == canonical_config_hash(
        {key: value for key, value in original.items() if key != "forward_validation"}
    )
    assert snapshot_config == original


def test_forward_handoff_requires_both_promotion_gates() -> None:
    candidate = get_candidate("trend_momentum_v2_enriched")
    density = _handoff().density
    with pytest.raises(ValueError, match="PROFITABILITY_RECOVERY_NOT_PASS"):
        build_forward_validation_handoff(
            candidate_id=candidate.candidate_id,
            candidate_version=candidate.version,
            strategy_rules=candidate.get_config(),
            dataset_hash="d" * 64,
            validation_evidence_ref="evidence.json",
            validation_evidence_hash="e" * 64,
            eligible_execution_symbols=("BTC/USDT", "ETH/USDT"),
            density=density,
            profitability_recovery=PromotionResult(eligible=False, failed_requirements=("gate",)),
            runtime_config={"strategy_rules": candidate.get_config()},
            frozen_at=datetime(2026, 8, 30, tzinfo=UTC),
        )


def test_valid_forward_handoff_resolves_testnet_forward() -> None:
    handoff = _handoff().model_dump(mode="json")
    snapshot_config = _snapshot_config(handoff)
    authorization = resolve_testnet_forward_authorization(
        snapshot_config=snapshot_config,
        snapshot_hash=canonical_config_hash(snapshot_config),
        symbol="BTC/USDT",
        execution_mode="BINANCE_TESTNET",
    )

    assert authorization.authorized is True
    assert authorization.candidate_id == "trend_momentum_v2_enriched"
    assert authorization.validation_evidence_hash == "e" * 64
    assert (
        resolve_entry_authority(
            production_authorized=False,
            production_strategy_id=None,
            forward_authorized=True,
            forward_strategy_id=authorization.candidate_id,
            execution_mode="BINANCE_TESTNET",
            operator_testnet_canary_enabled=True,
            explicit_testnet_canary=True,
        ).authority
        is EntryAuthority.TESTNET_FORWARD
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", "other"),
        ("validation_evidence_hash", "f" * 64),
        ("strategy_package_hash", "f" * 64),
    ],
)
def test_tampered_forward_handoff_fails_closed(field: str, value: str) -> None:
    handoff = _handoff().model_dump(mode="json")
    handoff[field] = value
    snapshot_config = _snapshot_config(handoff)

    authorization = resolve_testnet_forward_authorization(
        snapshot_config=snapshot_config,
        snapshot_hash=canonical_config_hash(snapshot_config),
        symbol="BTC/USDT",
        execution_mode="BINANCE_TESTNET",
    )

    assert authorization.authorized is False


def test_forward_handoff_is_denied_on_mainnet() -> None:
    handoff = _handoff().model_dump(mode="json")
    snapshot_config = _snapshot_config(handoff)
    authorization = resolve_testnet_forward_authorization(
        snapshot_config=snapshot_config,
        snapshot_hash=canonical_config_hash(snapshot_config),
        symbol="BTC/USDT",
        execution_mode="BINANCE_MAINNET",
    )

    assert authorization.authorized is False
    assert authorization.reason == "TESTNET_FORWARD_REQUIRES_BINANCE_TESTNET"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda config: config["strategy_rules"]["entry_rules"].update({"candidate_id": "operator_heuristic_v1"}),
        lambda config: config["forward_validation"].update({"validation_evidence_hash": "f" * 64}),
        lambda config: config["forward_validation"].update({"runtime_config_binding_hash": "sha256:" + "f" * 64}),
    ],
)
def test_runtime_or_handoff_tampering_is_denied(mutation) -> None:
    snapshot_config = _snapshot_config(_handoff().model_dump(mode="json"))
    mutation(snapshot_config)

    authorization = resolve_testnet_forward_authorization(
        snapshot_config=snapshot_config,
        snapshot_hash=canonical_config_hash(snapshot_config),
        symbol="BTC/USDT",
        execution_mode="BINANCE_TESTNET",
    )

    assert authorization.authorized is False


def test_v1_forward_handoff_is_explicitly_rejected() -> None:
    handoff = _handoff().model_dump(mode="json")
    handoff["schema_version"] = "forward-validation-handoff-v1"
    snapshot_config = _snapshot_config(handoff)

    authorization = resolve_testnet_forward_authorization(
        snapshot_config=snapshot_config,
        snapshot_hash=canonical_config_hash(snapshot_config),
        symbol="BTC/USDT",
        execution_mode="BINANCE_TESTNET",
    )

    assert authorization.authorized is False
    assert authorization.reason == "FORWARD_VALIDATION_V1_REJECTED"


def test_production_remains_prioritized_over_testnet_forward() -> None:
    authority = resolve_entry_authority(
        production_authorized=True,
        production_strategy_id="approved-production",
        forward_authorized=True,
        forward_strategy_id="forward-candidate",
        execution_mode="BINANCE_TESTNET",
        operator_testnet_canary_enabled=True,
        explicit_testnet_canary=True,
    )

    assert authority.authority is EntryAuthority.PRODUCTION


@pytest.mark.parametrize(
    ("canary_enabled", "expected"),
    [(False, EntryAuthority.NONE), (True, EntryAuthority.TESTNET_CANARY)],
)
def test_no_forward_preserves_existing_none_and_canary_fallbacks(
    canary_enabled: bool,
    expected: EntryAuthority,
) -> None:
    authority = resolve_entry_authority(
        production_authorized=False,
        production_strategy_id=None,
        forward_authorized=False,
        forward_strategy_id=None,
        execution_mode="BINANCE_TESTNET",
        operator_testnet_canary_enabled=canary_enabled,
        explicit_testnet_canary=canary_enabled,
    )

    assert authority.authority is expected
