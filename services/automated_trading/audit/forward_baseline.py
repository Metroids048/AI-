"""Immutable forward-decision snapshots and deterministic replay helpers.

This module is deliberately independent from exchange execution.  A snapshot
contains the exact closed bars and decision evidence used by one V2 cycle;
replay consumes only that payload and returns an explicit comparison report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.automated_trading.application.decision_service import (
    BarView,
    DecisionContext,
    DecisionOutcome,
    TimeframeView,
    evaluate_symbol,
)
from services.automated_trading.domain.candidates import CandidateLane
from services.strategy_library.canonical import canonical_hash

SNAPSHOT_SCHEMA_VERSION = "forward-decision-snapshot-v1"


@dataclass(frozen=True)
class ReplayComparison:
    """Explicit replay verdict; no mismatch is silently downgraded."""

    decision_match: bool
    feature_match: bool
    trade_candidate_match: bool
    mismatches: tuple[str, ...] = ()

    @property
    def first_divergence_point(self) -> str | None:
        return self.mismatches[0] if self.mismatches else None

    @property
    def reproducible(self) -> bool:
        return self.decision_match and self.feature_match and self.trade_candidate_match


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _bar_payload(symbol: str, bar: BarView) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timestamp": _iso(bar.timestamp),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }


def _candidate_payload(candidate: Any) -> dict[str, Any] | None:
    if candidate is None:
        return None
    reference = candidate.signal_reference_price
    stop_price = (
        reference - candidate.stop_distance if str(candidate.side) == "LONG" else reference + candidate.stop_distance
    )
    take_price = None
    if candidate.take_profit_distance is not None:
        take_price = (
            reference + candidate.take_profit_distance
            if str(candidate.side) == "LONG"
            else reference - candidate.take_profit_distance
        )
    return {
        "candidate_id": str(candidate.candidate_id),
        "cycle_id": str(candidate.cycle_id),
        "strategy_id": str(candidate.strategy_id),
        "strategy_version": str(candidate.strategy_version),
        "lane": str(candidate.lane),
        "candidate_type": str(candidate.candidate_type),
        "symbol": str(candidate.symbol),
        "side": str(candidate.side),
        "signal_candle_close_time": _iso(candidate.signal_candle_close_time),
        "signal_reference_price": str(candidate.signal_reference_price),
        "confidence": str(candidate.confidence),
        "stop_distance": str(candidate.stop_distance),
        "take_profit_distance": (
            str(candidate.take_profit_distance) if candidate.take_profit_distance is not None else None
        ),
        "stop_reference_price": str(stop_price),
        "take_profit_reference_price": str(take_price) if take_price is not None else None,
        "max_entry_drift_bps": str(candidate.max_entry_drift_bps),
        "expires_at": _iso(candidate.expires_at),
        "non_promotable": bool(candidate.non_promotable),
        "signal_context": [[str(key), str(value)] for key, value in candidate.signal_context],
    }


serialize_trade_candidate = _candidate_payload


def _normalise_funnel(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove wall-clock bookkeeping while preserving decision semantics."""
    normalized = dict(payload)
    normalized.pop("created_at", None)
    normalized.pop("funnel_id", None)
    stages = []
    for stage in normalized.get("stages", []):
        stage_copy = dict(stage)
        stage_copy.pop("recorded_at", None)
        stages.append(stage_copy)
    normalized["stages"] = stages
    return normalized


def _features(outcome: DecisionOutcome) -> dict[str, Any]:
    features: dict[str, Any] = {}
    for stage in outcome.funnel.stages:
        for key, value in stage.metrics:
            features[str(key)] = str(value)
    return dict(sorted(features.items()))


def build_decision_snapshot(
    *,
    cycle_id: str,
    decision_id: str,
    symbol: str,
    decision_time: datetime,
    strategy_commit: str,
    strategy_version: str,
    config_hash: str,
    market_data_source: str,
    entry_timeframe: TimeframeView,
    outcome: DecisionOutcome,
    initial_risk_inputs: dict[str, Any],
    cost_inputs: dict[str, Any],
    config_snapshot_id: str | None = None,
    already_evaluated_bars: frozenset[datetime] = frozenset(),
    execution_mode: str | None = None,
    engine_activation: str | None = None,
) -> dict[str, Any]:
    """Build a hash-sealed snapshot from one completed pure decision."""
    if outcome.symbol != symbol or outcome.cycle_id != cycle_id:
        raise ValueError("decision outcome identity does not match snapshot identity")
    if not entry_timeframe.bars:
        raise ValueError("forward snapshot requires at least one closed bar")
    bars = [_bar_payload(symbol, bar) for bar in entry_timeframe.bars]
    candidate = _candidate_payload(outcome.candidate)
    payload: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "decision_id": decision_id,
        "symbol": symbol,
        "decision_time": _iso(decision_time),
        "strategy_commit": strategy_commit,
        "strategy_version": strategy_version,
        "config_snapshot_id": config_snapshot_id,
        "config_hash": config_hash,
        "execution_mode": execution_mode,
        "engine_activation": engine_activation,
        "market_data_source": market_data_source,
        "timeframe": entry_timeframe.timeframe,
        "closed_bar_boundary": _iso(entry_timeframe.bars[-1].timestamp),
        "warmup_range": {
            "start": _iso(entry_timeframe.bars[0].timestamp),
            "end": _iso(entry_timeframe.bars[-1].timestamp),
            "count": len(bars),
        },
        "input_bars": bars,
        "already_evaluated_bars": sorted(_iso(item) for item in already_evaluated_bars),
        "decision": {
            "lane": str(outcome.lane),
            "strategy_id": outcome.funnel.strategy_id,
            "strategy_version": outcome.funnel.strategy_version,
            "terminal_stage": outcome.terminal_stage.value,
            "reason_code": outcome.reason_code.value,
            "funnel": _normalise_funnel(outcome.funnel.to_payload()),
        },
        "features": _features(outcome),
        "candidate": candidate,
        "trade_candidate_payload": candidate,
        "initial_risk_inputs": dict(initial_risk_inputs),
        "cost_inputs": dict(cost_inputs),
    }
    payload["snapshot_hash"] = canonical_hash(payload)
    return payload


def build_decision_snapshot_from_funnel(
    *,
    cycle_id: str,
    decision_id: str,
    symbol: str,
    decision_time: datetime,
    strategy_commit: str,
    strategy_version: str,
    config_hash: str,
    market_data_source: str,
    entry_timeframe: TimeframeView,
    funnel_payload: dict[str, Any],
    initial_risk_inputs: dict[str, Any],
    cost_inputs: dict[str, Any],
    config_snapshot_id: str | None = None,
    candidate_payload: dict[str, Any] | None = None,
    already_evaluated_bars: frozenset[datetime] = frozenset(),
    execution_mode: str | None = None,
    engine_activation: str | None = None,
) -> dict[str, Any]:
    """Capture a snapshot when the active adapter emits only a funnel payload."""
    if not entry_timeframe.bars:
        raise ValueError("forward snapshot requires at least one closed bar")
    stages = funnel_payload.get("stages")
    features: dict[str, Any] = {}
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict) and isinstance(stage.get("metrics"), dict):
                features.update({str(key): str(value) for key, value in stage["metrics"].items()})
    payload: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "decision_id": decision_id,
        "symbol": symbol,
        "decision_time": _iso(decision_time),
        "strategy_commit": strategy_commit,
        "strategy_version": strategy_version,
        "config_snapshot_id": config_snapshot_id,
        "config_hash": config_hash,
        "execution_mode": execution_mode,
        "engine_activation": engine_activation,
        "market_data_source": market_data_source,
        "timeframe": entry_timeframe.timeframe,
        "closed_bar_boundary": _iso(entry_timeframe.bars[-1].timestamp),
        "warmup_range": {
            "start": _iso(entry_timeframe.bars[0].timestamp),
            "end": _iso(entry_timeframe.bars[-1].timestamp),
            "count": len(entry_timeframe.bars),
        },
        "input_bars": [_bar_payload(symbol, bar) for bar in entry_timeframe.bars],
        "already_evaluated_bars": sorted(_iso(item) for item in already_evaluated_bars),
        "decision": {
            "lane": str(funnel_payload.get("lane") or "PRODUCTION"),
            "strategy_id": str(funnel_payload.get("strategy_id") or "unknown"),
            "strategy_version": strategy_version,
            "terminal_stage": str(funnel_payload.get("terminal_stage") or "UNKNOWN"),
            "reason_code": str(funnel_payload.get("reason_code") or "UNKNOWN"),
            "funnel": _normalise_funnel(funnel_payload),
        },
        "features": dict(sorted(features.items())),
        "candidate": candidate_payload,
        "trade_candidate_payload": candidate_payload,
        "initial_risk_inputs": dict(initial_risk_inputs),
        "cost_inputs": dict(cost_inputs),
    }
    payload["snapshot_hash"] = canonical_hash(payload)
    return payload


def _parse_bar(payload: dict[str, Any]) -> BarView:
    return BarView(
        timestamp=datetime.fromisoformat(str(payload["timestamp"])).astimezone(UTC),
        open=Decimal(str(payload["open"])),
        high=Decimal(str(payload["high"])),
        low=Decimal(str(payload["low"])),
        close=Decimal(str(payload["close"])),
        volume=Decimal(str(payload["volume"])),
    )


def replay_decision_snapshot(snapshot: dict[str, Any]) -> DecisionOutcome:
    """Replay one sampling-lane snapshot using only its frozen input bars."""
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported forward snapshot schema")
    expected_hash = snapshot.get("snapshot_hash")
    content = dict(snapshot)
    content.pop("snapshot_hash", None)
    if expected_hash != canonical_hash(content):
        raise ValueError("FORWARD_SNAPSHOT_HASH_MISMATCH")
    decision = snapshot["decision"]
    candidate = snapshot.get("candidate")
    candidate_id = str(candidate["candidate_id"]) if isinstance(candidate, dict) else None
    lane = CandidateLane(str(decision["lane"]))
    if lane is not CandidateLane.TESTNET_SAMPLING:
        raise ValueError("FORWARD_REPLAY_UNSUPPORTED_LANE")
    bars = tuple(_parse_bar(item) for item in snapshot["input_bars"])
    now = datetime.fromisoformat(str(snapshot["decision_time"])).astimezone(UTC)
    already = frozenset(
        datetime.fromisoformat(str(item)).astimezone(UTC) for item in snapshot.get("already_evaluated_bars", [])
    )
    return evaluate_symbol(
        DecisionContext(
            cycle_id=str(snapshot["cycle_id"]),
            symbol=str(snapshot["symbol"]),
            lane=lane,
            strategy_id=str(decision["strategy_id"]),
            strategy_version=str(decision["strategy_version"]),
            entry_timeframe=TimeframeView(timeframe=str(snapshot["timeframe"]), bars=bars),
            now=now,
            already_evaluated_bars=already,
            candidate_id_factory=(lambda: candidate_id) if candidate_id else __import__("uuid").uuid4,
        )
    )


def compare_decision_snapshot(snapshot: dict[str, Any]) -> ReplayComparison:
    """Compare a snapshot with a fresh deterministic replay."""
    mismatches: list[str] = []
    try:
        replay = replay_decision_snapshot(snapshot)
    except Exception as exc:  # noqa: BLE001
        return ReplayComparison(False, False, False, (f"FIRST_DIVERGENCE_POINT:{type(exc).__name__}:{exc}",))
    decision = snapshot["decision"]
    if replay.terminal_stage.value != decision["terminal_stage"]:
        mismatches.append("FIRST_DIVERGENCE_POINT:decision.terminal_stage")
    if replay.reason_code.value != decision["reason_code"]:
        mismatches.append("decision.reason_code")
    replay_features = _features(replay)
    feature_match = replay_features == snapshot.get("features", {})
    if not feature_match:
        mismatches.append("features")
    replay_candidate = _candidate_payload(replay.candidate)
    candidate_match = replay_candidate == snapshot.get("trade_candidate_payload")
    if not candidate_match:
        mismatches.append("trade_candidate_payload")
    decision_match = not any(
        item.startswith("FIRST_DIVERGENCE_POINT") or item == "decision.reason_code" for item in mismatches
    )
    return ReplayComparison(
        decision_match=decision_match,
        feature_match=feature_match,
        trade_candidate_match=candidate_match,
        mismatches=tuple(mismatches),
    )


def build_shadow_records(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Create ACTUAL/R1/R2/R3 evidence rows without any execution effect."""
    candidate = snapshot.get("trade_candidate_payload")
    if not candidate:
        return []
    cost_inputs = snapshot.get("cost_inputs", {})
    expected_cost_r = cost_inputs.get("expected_cost_r")
    if expected_cost_r is None:
        try:
            fee_fraction = (
                Decimal(str(cost_inputs.get("maker_bps", "2"))) + Decimal(str(cost_inputs.get("taker_bps", "5")))
            ) / Decimal("10000")
            stop_fraction = Decimal(str(candidate["stop_distance"])) / Decimal(str(candidate["signal_reference_price"]))
            if stop_fraction > 0:
                expected_cost_r = str(fee_fraction / stop_fraction)
        except (KeyError, ValueError, ArithmeticError):
            expected_cost_r = None
    base = {
        "snapshot_hash": snapshot["snapshot_hash"],
        "symbol": snapshot["symbol"],
        "decision_time": snapshot["decision_time"],
        "direction": candidate.get("side"),
        "entry_reference": candidate.get("signal_reference_price"),
        "execution_side_effects": [],
        "outcome_status": "PENDING",
    }
    return [
        {"variant": "ACTUAL", **base, "payload": {"candidate": candidate}},
        {
            "variant": "R1_SHADOW",
            **base,
            "payload": {
                "counterfactual": "equal_risk",
                "equal_risk_inputs": snapshot.get("initial_risk_inputs", {}),
                "source_candidate": candidate,
            },
        },
        {
            "variant": "R2_SHADOW",
            **base,
            "payload": {"expected_cost_r": expected_cost_r},
        },
        {
            "variant": "R3_SHADOW",
            **base,
            "payload": {
                "entry_quality_features": snapshot.get("features", {}),
                "execution_side_effects": [],
            },
        },
        {
            "variant": "P2_SHADOW",
            **base,
            "payload": {
                "policy": "P2",
                "counterfactual": "0.75R_to_0.05R_net__1.25R_to_0.50R_net",
                "execution_side_effects": [],
            },
        },
        {
            "variant": "P3_SHADOW",
            **base,
            "payload": {
                "policy": "P3",
                "counterfactual": "1.00R_to_0.10R_net",
                "execution_side_effects": [],
            },
        },
    ]


def build_shadow_outcome(
    snapshot: dict[str, Any],
    *,
    direction: str,
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    commission: Decimal,
    funding: Decimal = Decimal("0"),
    exit_reason: str,
) -> dict[str, Any]:
    """Normalize one confirmed exchange exit into R-based Shadow outcomes."""
    candidate = snapshot.get("trade_candidate_payload") or {}
    stop_distance = Decimal(str(candidate.get("stop_distance") or "0"))
    risk_amount = stop_distance * quantity
    gross_pnl = (exit_price - entry_price) * quantity if direction == "long" else (entry_price - exit_price) * quantity
    net_pnl = gross_pnl - commission + funding

    def _r(value: Decimal) -> str | None:
        return str(value / risk_amount) if risk_amount > 0 else None

    return {
        "exit_reason": exit_reason,
        "entry_price": str(entry_price),
        "exit_price": str(exit_price),
        "quantity": str(quantity),
        "gross_pnl": str(gross_pnl),
        "net_pnl": str(net_pnl),
        "gross_R": _r(gross_pnl),
        "net_R": _r(net_pnl),
        "commission_R": _r(commission),
        "funding_R": _r(funding),
        "MFE_R": None,
        "MAE_R": None,
        "outcome_status": "CLOSED",
    }
