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
        "schema_version": 3,
        "strategy_key": AUTO_PAPER_TECHNICAL_KEY,
        "rules_hash": strategy_rules_hash(rules),
        "production_authorization": {
            "state": "APPROVED",
            "candidate_id": "trend_momentum_v2_enriched",
            "candidate_version": "2.0.0",
            "rules_hash": strategy_rules_hash(rules),
            "config_snapshot_hash": snapshot_hash,
            "eligible_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
            "validation_evidence_ref": "artifacts/validation/approved-evidence.json",
            "approval": {"approved_by": "operator@example", "approved_at": "2026-08-12T00:00:00+00:00"},
        },
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
    manifest["production_authorization"]["candidate_id"] = "aggressive_multi_regime_v1"
    manifest["production_authorization"]["candidate_version"] = "1.0.0"
    rules = get_candidate("aggressive_multi_regime_v1").get_config()
    strategy_rules = StrategyRules(**rules)
    snapshot["strategy_rules"] = rules
    manifest["rules_hash"] = strategy_rules_hash(strategy_rules)
    manifest["production_authorization"]["rules_hash"] = strategy_rules_hash(strategy_rules)
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
        (lambda manifest: manifest["production_authorization"].update({"rules_hash": "wrong"}), "BTC/USDT"),
        (lambda manifest: manifest["production_authorization"].update({"eligible_symbols": ["BTC/USDT"]}), "BTC/USDT"),
        (lambda manifest: manifest["production_authorization"].update({"config_snapshot_hash": "stale"}), "BTC/USDT"),
        (lambda manifest: manifest["production_authorization"].update({"state": "PENDING"}), "BTC/USDT"),
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
