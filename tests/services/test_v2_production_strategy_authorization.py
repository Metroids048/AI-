"""V2 production authority is explicit, snapshot-bound, and fail-closed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.automated_trading.application.production_strategy import (
    NO_AUTHORIZED_PRODUCTION_STRATEGY,
    EntryAuthority,
    evaluate_authorized_production_strategy,
    resolve_entry_authority,
    resolve_production_authorization,
)
from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.candidates.registry import get_candidate
from shared.models import StrategyRules


def test_entry_authority_prefers_approved_production_over_enabled_canary() -> None:
    authority = resolve_entry_authority(
        production_authorized=True,
        production_strategy_id="trend_momentum_v2_enriched",
        execution_mode="BINANCE_TESTNET",
        operator_testnet_canary_enabled=True,
    )

    assert authority.authority is EntryAuthority.PRODUCTION
    assert authority.active_strategy_id == "trend_momentum_v2_enriched"
    assert authority.promotion_eligible is True


def test_pending_production_can_only_use_enabled_binance_testnet_canary() -> None:
    canary = resolve_entry_authority(
        production_authorized=False,
        production_strategy_id=None,
        execution_mode="BINANCE_TESTNET",
        operator_testnet_canary_enabled=True,
    )
    disabled = resolve_entry_authority(
        production_authorized=False,
        production_strategy_id=None,
        execution_mode="BINANCE_TESTNET",
        operator_testnet_canary_enabled=False,
    )
    mainnet = resolve_entry_authority(
        production_authorized=False,
        production_strategy_id=None,
        execution_mode="BINANCE_MAINNET",
        operator_testnet_canary_enabled=True,
    )

    assert canary.authority is EntryAuthority.TESTNET_CANARY
    assert canary.promotion_eligible is False
    assert disabled.authority is EntryAuthority.NONE
    assert mainnet.authority is EntryAuthority.NONE


@pytest.fixture
def candidate_rules() -> dict:
    return get_candidate("trend_momentum_v2_enriched").get_config()


@pytest.fixture
def approved_manifest(tmp_path, monkeypatch, candidate_rules) -> tuple[dict, dict, str]:
    from services.automated_trading.application import production_strategy

    rules = StrategyRules(**candidate_rules)
    snapshot = {"strategy_rules": candidate_rules, "execution_profile": {"strategy_lane": "directional"}}
    snapshot_hash = "sha256:immutable-active-snapshot"
    manifest = {
        "schema_version": 4,
        "strategy_key": AUTO_PAPER_TECHNICAL_KEY,
        "strategy_id": "trend_momentum_v2_enriched",
        "strategy_version": "2.0.0",
        "rules_hash": strategy_rules_hash(rules),
        "commit_sha": "a" * 40,
        "configured_execution_scope": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        "eligible_execution_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        "research_symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"],
        "validation_evidence": {
            "dataset_hash": "dataset-hash",
            "report_ref": "artifacts/validation/approved-evidence.json",
            "conclusion": "APPROVED",
        },
        "golden_behavior_ref": "artifacts/golden/approved.json",
        "authorization_state": "APPROVED",
        "approval": {
            "approved_by": "operator@example",
            "approved_at": "2026-08-12T00:00:00+00:00",
            "rationale": "test fixture",
        },
        "config_snapshot_hash": snapshot_hash,
        "effective_at": "2026-08-12T00:00:00+00:00",
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(production_strategy, "_manifest_path", lambda: manifest_path)
    return manifest, snapshot, snapshot_hash


def test_default_manifest_is_pending_and_cannot_authorize() -> None:
    authorization = resolve_production_authorization(
        snapshot_config={"strategy_rules": {}},
        snapshot_hash="sha256:any",
        symbol="BTC/USDT",
    )

    assert authorization.authorized is False
    assert authorization.reason == NO_AUTHORIZED_PRODUCTION_STRATEGY


def test_research_only_candidate_cannot_authorize_even_with_valid_hashes(approved_manifest) -> None:
    _manifest, snapshot, snapshot_hash = approved_manifest
    from services.automated_trading.application import production_strategy

    manifest = json.loads(production_strategy._manifest_path().read_text(encoding="utf-8"))
    manifest["strategy_id"] = "aggressive_multi_regime_v1"
    manifest["strategy_version"] = "1.0.0"
    rules = get_candidate("aggressive_multi_regime_v1").get_config()
    strategy_rules = StrategyRules(**rules)
    snapshot["strategy_rules"] = rules
    manifest["rules_hash"] = strategy_rules_hash(strategy_rules)
    production_strategy._manifest_path().write_text(json.dumps(manifest), encoding="utf-8")
    authorization = resolve_production_authorization(
        snapshot_config=snapshot,
        snapshot_hash=snapshot_hash,
        symbol="BTC/USDT",
    )
    assert authorization.authorized is False


@pytest.mark.parametrize(
    ("mutation", "symbol"),
    [
        (lambda manifest: manifest.update({"rules_hash": "wrong"}), "BTC/USDT"),
        (lambda manifest: manifest.update({"eligible_execution_symbols": ["BTC/USDT"]}), "BTC/USDT"),
        (lambda manifest: manifest.update({"config_snapshot_hash": "stale"}), "BTC/USDT"),
        (lambda manifest: manifest.update({"authorization_state": "PENDING"}), "BTC/USDT"),
    ],
)
def test_invalid_production_authorization_fails_closed(approved_manifest, mutation, symbol) -> None:
    manifest, snapshot, snapshot_hash = approved_manifest
    mutation(manifest)
    from services.automated_trading.application import production_strategy

    production_strategy._manifest_path().write_text(json.dumps(manifest), encoding="utf-8")
    authorization = resolve_production_authorization(
        snapshot_config=snapshot,
        snapshot_hash=snapshot_hash,
        symbol=symbol,
    )

    assert authorization.authorized is False
    assert authorization.reason == NO_AUTHORIZED_PRODUCTION_STRATEGY


def test_approved_manifest_with_partial_scope_cannot_authorize_any_execution_symbol(approved_manifest) -> None:
    manifest, snapshot, snapshot_hash = approved_manifest
    manifest["eligible_execution_symbols"] = ["BTC/USDT"]
    from services.automated_trading.application import production_strategy

    production_strategy._manifest_path().write_text(json.dumps(manifest), encoding="utf-8")

    authorizations = tuple(
        resolve_production_authorization(
            snapshot_config=snapshot,
            snapshot_hash=snapshot_hash,
            symbol=symbol,
        )
        for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS
    )

    assert all(not authorization.authorized for authorization in authorizations)
    assert {authorization.reason for authorization in authorizations} == {NO_AUTHORIZED_PRODUCTION_STRATEGY}


def test_full_scope_manifest_without_approval_cannot_authorize_any_execution_symbol(approved_manifest) -> None:
    manifest, snapshot, snapshot_hash = approved_manifest
    manifest["authorization_state"] = "PENDING"
    from services.automated_trading.application import production_strategy

    production_strategy._manifest_path().write_text(json.dumps(manifest), encoding="utf-8")

    authorizations = tuple(
        resolve_production_authorization(
            snapshot_config=snapshot,
            snapshot_hash=snapshot_hash,
            symbol=symbol,
        )
        for symbol in AUTO_SIMULATION_EXECUTION_SYMBOLS
    )

    assert all(not authorization.authorized for authorization in authorizations)
    assert {authorization.reason for authorization in authorizations} == {NO_AUTHORIZED_PRODUCTION_STRATEGY}


def test_approved_authorization_binds_candidate_rules_snapshot_and_scope(approved_manifest) -> None:
    _manifest, snapshot, snapshot_hash = approved_manifest

    authorization = resolve_production_authorization(
        snapshot_config=snapshot,
        snapshot_hash=snapshot_hash,
        symbol="ETH/USDT",
    )

    assert authorization.authorized is True
    assert authorization.candidate_id == "trend_momentum_v2_enriched"
    assert authorization.candidate_version == "2.0.0"
    assert authorization.validation_evidence_ref == "artifacts/validation/approved-evidence.json"


def test_approved_adapter_preserves_production_geometry_and_provenance(approved_manifest, monkeypatch) -> None:
    _manifest, snapshot, snapshot_hash = approved_manifest
    authorization = resolve_production_authorization(
        snapshot_config=snapshot,
        snapshot_hash=snapshot_hash,
        symbol="BTC/USDT",
    )
    now = datetime(2026, 8, 12, tzinfo=UTC)
    from services.automated_trading.application import production_strategy
    from services.execution.decision_pipeline import DecisionPipelineResult
    from shared.models import TradeSide

    result = DecisionPipelineResult(
        direction=TradeSide.LONG,
        should_trade=True,
        reason="ensemble_meta_label_passed",
        reference_price=Decimal("100"),
        bar_time=now,
        signals=[],
        ensemble=None,
        meta_label=None,
        veto_result=None,
        confidence_multiplier=0.75,
        atr=Decimal("2"),
        volatility_context={"regime": "trend"},
        trace={"decision_trace": "preserved"},
    )
    monkeypatch.setattr(production_strategy.DecisionPipeline, "evaluate", lambda *_args, **_kwargs: result)

    decision = evaluate_authorized_production_strategy(
        authorization=authorization,
        data_repo=object(),
        cycle_id="cycle-approved",
        symbol="BTC/USDT",
        now=now,
    )

    assert decision.candidate is not None
    assert decision.candidate.lane.value == "PRODUCTION"
    assert decision.candidate.candidate_type.value == "PRIMARY"
    assert decision.candidate.non_promotable is False
    assert decision.candidate.stop_distance == Decimal("4")
    assert decision.candidate.take_profit_distance == Decimal("8")
    assert decision.candidate.signal_reference_price == Decimal("100")
    assert (
        dict(decision.candidate.signal_context)["validation_evidence_ref"]
        == "artifacts/validation/approved-evidence.json"
    )
