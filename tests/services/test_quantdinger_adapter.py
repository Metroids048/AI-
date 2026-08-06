"""Contracts for the isolated, shadow-only QuantDinger strategy bridge."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.automated_trading.application.entry_service import (
    EntryDecision,
    EntryExecutionStatus,
    EntryGateResult,
    EntryRuntimeContext,
    evaluate_entry,
    execute_entry,
)
from services.automated_trading.application.reconciliation_service import ReconciliationStatus
from services.automated_trading.domain.enums import V2CandidateType
from services.automated_trading.infrastructure.market_snapshot_provider import PreSubmitMarketSnapshot
from services.automated_trading.infrastructure.runtime_lock import EngineActivation
from services.automated_trading.observability.decision_funnel import (
    DecisionReasonCode,
    FunnelStage,
    StageOutcome,
)
from services.strategy_library.adapters.quantdinger import (
    QuantDingerContractError,
    QuantDingerSignal,
    build_shadow_candidate,
    build_shadow_funnel_record,
    compile_strategy_manifest,
    parse_shadow_signal,
)

SOURCE = """
def initialize(context):
    context.set_universe(["BTCUSDT", "ETH/USDT:USDT"])
    context.subscribe(frequency="15m")
    context.set_warmup(80)
    context.set_default_protection(stop_loss_pct=0.01, take_profit_pct=0.02)

def handle_data(context, data):
    return factor("rsi_14") + indicator("macd")
"""


def _signal(*, symbol: str = "BTC/USDT") -> QuantDingerSignal:
    closed_at = datetime(2026, 8, 5, 12, tzinfo=UTC)
    return QuantDingerSignal(
        strategy_id="qd-rsi-macd",
        strategy_version="1.0.0",
        symbol=symbol,
        timeframe="15m",
        side="LONG",
        signal_candle_close_time=closed_at,
        signal_reference_price=Decimal("65000"),
        confidence=Decimal("0.7"),
        stop_distance=Decimal("650"),
        take_profit_distance=Decimal("1300"),
        max_entry_drift_bps=Decimal("25"),
        expires_at=closed_at + timedelta(minutes=15),
    )


def _snapshot() -> PreSubmitMarketSnapshot:
    return PreSubmitMarketSnapshot(
        symbol="BTC/USDT",
        current_price=Decimal("65000"),
        atr=Decimal("500"),
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("20"),
        last_update=datetime(2026, 8, 5, 12, tzinfo=UTC),
    )


def _shadow_payload(*, manifest_code_hash: str | None = None) -> dict[str, object]:
    signal = _signal()
    return {
        "manifest_code_hash": manifest_code_hash or compile_strategy_manifest(SOURCE).code_hash,
        "strategy_id": signal.strategy_id,
        "strategy_version": signal.strategy_version,
        "symbol": signal.symbol,
        "timeframe": signal.timeframe,
        "side": signal.side,
        "signal_candle_close_time": signal.signal_candle_close_time.isoformat(),
        "signal_reference_price": str(signal.signal_reference_price),
        "confidence": str(signal.confidence),
        "stop_distance": str(signal.stop_distance),
        "take_profit_distance": str(signal.take_profit_distance),
        "max_entry_drift_bps": str(signal.max_entry_drift_bps),
        "expires_at": signal.expires_at.isoformat(),
    }


def test_static_manifest_collects_provenance_dependencies_and_protection() -> None:
    manifest = compile_strategy_manifest(SOURCE)

    assert manifest.code_hash == hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
    assert manifest.instruments == ("BTC/USDT", "ETH/USDT")
    assert manifest.timeframe == "15m"
    assert manifest.warmup_bars == 80
    assert manifest.factor_dependencies == ("macd", "rsi_14")
    assert manifest.handlers == ("handle_data",)
    assert manifest.protection.stop_loss_pct == Decimal("0.01")
    assert manifest.protection.take_profit_pct == Decimal("0.02")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("import os\n" + SOURCE, "imports are not permitted"),
        (SOURCE.replace('return factor("rsi_14") + indicator("macd")', 'return order("BTCUSDT", 1)'), "execution API"),
        (
            SOURCE.replace('context.subscribe(frequency="15m")', 'context.subscribe(frequency="2m")'),
            "unsupported subscription",
        ),
        (
            SOURCE.replace("take_profit_pct=0.02", "take_profit_pct=1e9999"),
            "take_profit_pct must be finite",
        ),
        (SOURCE.replace('["BTCUSDT", "ETH/USDT:USDT"]', "symbols"), "set_universe must use literal values"),
    ],
)
def test_static_manifest_rejects_unsafe_or_nonportable_source(source: str, message: str) -> None:
    with pytest.raises(QuantDingerContractError, match=message):
        compile_strategy_manifest(source)


def test_external_signal_becomes_nonpromotable_shadow_candidate() -> None:
    candidate = build_shadow_candidate(compile_strategy_manifest(SOURCE), _signal(), cycle_id="cycle-1")

    assert candidate.lane == "SHADOW"
    assert candidate.candidate_type.value == "RESEARCH"
    assert candidate.non_promotable is True
    assert dict(candidate.signal_context)["code_hash"] == hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()

    with pytest.raises(ValueError, match="SHADOW lane is reserved"):
        replace(candidate, candidate_type=V2CandidateType.PRIMARY)


def test_shadow_candidate_cannot_reach_exchange_submission() -> None:
    candidate = build_shadow_candidate(compile_strategy_manifest(SOURCE), _signal(), cycle_id="cycle-1")
    runtime = EntryRuntimeContext(
        engine_activation=EngineActivation.ACTIVE,
        execution_mode="BINANCE_TESTNET",
        reconciliation_status=ReconciliationStatus.HEALTHY,
    )
    adapter = MagicMock()

    gate = evaluate_entry(candidate, runtime, _snapshot())
    result = execute_entry(
        candidate,
        gate,
        _snapshot(),
        adapter=adapter,
        intent_id="intent-1",
        quantity=Decimal("0.01"),
        leverage=1,
        engine_activation=EngineActivation.ACTIVE,
    )

    assert gate.approved is False
    assert result.status is EntryExecutionStatus.NOT_ATTEMPTED
    adapter.submit_market_order.assert_not_called()

    bypass_attempt = execute_entry(
        candidate,
        EntryGateResult(decision=EntryDecision.APPROVED, reason_code="OK"),
        _snapshot(),
        adapter=adapter,
        intent_id="intent-2",
        quantity=Decimal("0.01"),
        leverage=1,
        engine_activation=EngineActivation.ACTIVE,
    )
    assert bypass_attempt.status is EntryExecutionStatus.NOT_ATTEMPTED
    adapter.submit_market_order.assert_not_called()


def test_shadow_converter_rejects_external_execution_symbol() -> None:
    source = SOURCE.replace('"ETH/USDT:USDT"', '"SOL/USDT"')
    with pytest.raises(QuantDingerContractError, match="outside the BTC/ETH execution universe"):
        build_shadow_candidate(compile_strategy_manifest(source), _signal(symbol="SOL/USDT"), cycle_id="cycle-1")


def test_shadow_converter_requires_signal_protection_to_match_manifest() -> None:
    signal = _signal()
    mismatched = replace(signal, stop_distance=Decimal("600"))

    with pytest.raises(QuantDingerContractError, match="stop distance differs"):
        build_shadow_candidate(compile_strategy_manifest(SOURCE), mismatched, cycle_id="cycle-1")


def test_shadow_signal_parser_binds_artifact_to_compiled_manifest() -> None:
    manifest = compile_strategy_manifest(SOURCE)

    parsed = parse_shadow_signal(_shadow_payload(manifest_code_hash=manifest.code_hash), manifest=manifest)

    assert parsed == _signal()

    canonicalized = parse_shadow_signal(
        {**_shadow_payload(manifest_code_hash=manifest.code_hash), "symbol": "btcusdt"},
        manifest=manifest,
    )
    assert canonicalized.symbol == "BTC/USDT"

    with pytest.raises(QuantDingerContractError, match="manifest_code_hash"):
        parse_shadow_signal(_shadow_payload(manifest_code_hash="wrong-source"), manifest=manifest)


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("signal_reference_price", "NaN", "must be finite"),
        ("signal_candle_close_time", "2026-08-05T12:00:00", "timezone-aware"),
        ("unexpected", "not-allowed", "schema mismatch"),
    ],
)
def test_shadow_signal_parser_fails_closed_on_invalid_event_schema(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _shadow_payload()
    payload[field] = value

    with pytest.raises(QuantDingerContractError, match=message):
        parse_shadow_signal(payload, manifest=compile_strategy_manifest(SOURCE))


def test_shadow_candidate_records_terminal_funnel_without_intent_or_exchange() -> None:
    manifest = compile_strategy_manifest(SOURCE)
    candidate = build_shadow_candidate(
        manifest,
        parse_shadow_signal(_shadow_payload(manifest_code_hash=manifest.code_hash), manifest=manifest),
        cycle_id="cycle-1",
    )

    funnel = build_shadow_funnel_record(candidate)

    assert funnel.reason_code is DecisionReasonCode.SHADOW_MODE_NO_SUBMIT
    assert funnel.terminal_stage is FunnelStage.MANIFEST_EVALUATED
    assert funnel.stage_for(FunnelStage.MANIFEST_EVALUATED).outcome is StageOutcome.SKIPPED
    assert FunnelStage.INTENT_CREATED not in [stage.stage for stage in funnel.stages]
    assert FunnelStage.EXCHANGE_SUBMITTED not in [stage.stage for stage in funnel.stages]
