from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.automated_trading.application.decision_service import (
    BarView,
    DecisionContext,
    TimeframeView,
    evaluate_symbol,
)
from services.automated_trading.audit.forward_baseline import (
    build_decision_snapshot,
    build_shadow_outcome,
    build_shadow_records,
    compare_decision_snapshot,
)
from services.automated_trading.domain.candidates import CandidateLane
from services.automated_trading.infrastructure.models import V2DecisionSnapshot
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


def _bars() -> TimeframeView:
    start = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
    bars = []
    price = Decimal("100")
    for index in range(80):
        delta = Decimal("0.1") if index < 70 else (Decimal("0.4") if index % 2 == 0 else Decimal("-0.2"))
        close = price + delta
        bars.append(
            BarView(
                timestamp=start + timedelta(minutes=15 * index + 15),
                open=price,
                high=close + Decimal("0.1"),
                low=price - Decimal("0.1"),
                close=close,
                volume=Decimal("10"),
            )
        )
        price = close
    return TimeframeView(timeframe="15m", bars=tuple(bars))


def test_forward_snapshot_replays_candidate_and_features_without_drift() -> None:
    timeframe = _bars()
    outcome = evaluate_symbol(
        DecisionContext(
            cycle_id="cycle-1",
            symbol="BTC/USDT",
            lane=CandidateLane.TESTNET_SAMPLING,
            strategy_id="testnet_sampling_v2",
            strategy_version="2.0.0",
            entry_timeframe=timeframe,
            now=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        )
    )
    assert outcome.candidate is not None, outcome.reason_code.value
    snapshot = build_decision_snapshot(
        cycle_id="cycle-1",
        decision_id="decision-1",
        symbol="BTC/USDT",
        decision_time=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        strategy_commit="abc123",
        strategy_version="2.0.0",
        config_hash="config-hash",
        market_data_source="runtime_data_repository",
        entry_timeframe=timeframe,
        outcome=outcome,
        initial_risk_inputs={"risk_per_trade": "0.05"},
        cost_inputs={"maker_bps": "2", "taker_bps": "5"},
    )

    comparison = compare_decision_snapshot(snapshot)

    assert snapshot["snapshot_hash"]
    assert comparison.decision_match is True
    assert comparison.feature_match is True
    assert comparison.trade_candidate_match is True
    assert comparison.mismatches == ()


def test_forward_snapshot_preserves_duplicate_bar_guard() -> None:
    timeframe = _bars()
    decision_bar = timeframe.last_closed
    assert decision_bar is not None
    outcome = evaluate_symbol(
        DecisionContext(
            cycle_id="cycle-duplicate",
            symbol="BTC/USDT",
            lane=CandidateLane.TESTNET_SAMPLING,
            strategy_id="testnet_sampling_v2",
            strategy_version="2.0.0",
            entry_timeframe=timeframe,
            now=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
            already_evaluated_bars=frozenset({decision_bar.timestamp}),
        )
    )
    snapshot = build_decision_snapshot(
        cycle_id="cycle-duplicate",
        decision_id="decision-duplicate",
        symbol="BTC/USDT",
        decision_time=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        strategy_commit="abc123",
        strategy_version="2.0.0",
        config_hash="config-hash",
        market_data_source="runtime_data_repository",
        entry_timeframe=timeframe,
        outcome=outcome,
        initial_risk_inputs={"risk_per_trade": "0.05"},
        cost_inputs={"maker_bps": "2", "taker_bps": "5"},
        already_evaluated_bars=frozenset({decision_bar.timestamp}),
        execution_mode="BINANCE_TESTNET",
        engine_activation="ACTIVE",
    )
    comparison = compare_decision_snapshot(snapshot)
    assert snapshot["already_evaluated_bars"] == [decision_bar.timestamp.isoformat()]
    assert comparison.reproducible is True


def test_shadow_records_are_side_effect_free_and_have_three_counterfactuals() -> None:
    timeframe = _bars()
    outcome = evaluate_symbol(
        DecisionContext(
            cycle_id="cycle-2",
            symbol="ETH/USDT",
            lane=CandidateLane.TESTNET_SAMPLING,
            strategy_id="testnet_sampling_v2",
            strategy_version="2.0.0",
            entry_timeframe=timeframe,
            now=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        )
    )
    assert outcome.candidate is not None, outcome.reason_code.value
    snapshot = build_decision_snapshot(
        cycle_id="cycle-2",
        decision_id="decision-2",
        symbol="ETH/USDT",
        decision_time=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        strategy_commit="abc123",
        strategy_version="2.0.0",
        config_hash="config-hash",
        market_data_source="runtime_data_repository",
        entry_timeframe=timeframe,
        outcome=outcome,
        initial_risk_inputs={"risk_per_trade": "0.05"},
        cost_inputs={"maker_bps": "2", "taker_bps": "5"},
    )

    rows = build_shadow_records(snapshot)

    assert {row["variant"] for row in rows} == {
        "ACTUAL",
        "R1_SHADOW",
        "R2_SHADOW",
        "R3_SHADOW",
        "P2_SHADOW",
        "P3_SHADOW",
    }
    assert all(row["execution_side_effects"] == [] for row in rows)
    outcome = build_shadow_outcome(
        snapshot,
        direction="long",
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        quantity=Decimal("1"),
        commission=Decimal("0.1"),
        exit_reason="takeprofit",
    )
    assert outcome["outcome_status"] == "CLOSED"
    assert outcome["gross_R"] is not None
    assert outcome["net_R"] is not None


def test_forward_snapshot_repository_is_append_only_and_links_shadow_rows(db_session) -> None:
    snapshot = {
        "schema_version": "forward-decision-snapshot-v1",
        "snapshot_hash": "hash-1",
        "cycle_id": "cycle-repo",
        "decision_id": "decision-repo",
        "symbol": "BTC/USDT",
    }
    repo = AutomatedTradingRepository(db_session)

    row = repo.append_forward_snapshot(
        snapshot_id="snapshot-1",
        cycle_id="cycle-repo",
        decision_id="decision-repo",
        symbol="BTC/USDT",
        decision_time=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        snapshot_hash="hash-1",
        payload=snapshot,
    )
    shadows = repo.append_shadow_records(
        snapshot_id=row.snapshot_id,
        records=build_shadow_records(
            {
                **snapshot,
                "trade_candidate_payload": {
                    "candidate_id": "candidate-1",
                    "side": "LONG",
                    "signal_reference_price": "100",
                },
                "decision_time": "2026-08-14T20:01:00+00:00",
                "features": {},
                "cost_inputs": {},
            }
        ),
    )

    assert isinstance(row, V2DecisionSnapshot)
    assert len(shadows) == 6
    assert len(repo.list_forward_snapshots(cycle_id="cycle-repo")) == 1
    assert len(repo.list_shadow_records(snapshot_id="snapshot-1")) == 6


def test_forward_snapshot_update_is_rejected(db_session) -> None:
    repo = AutomatedTradingRepository(db_session)
    row = repo.append_forward_snapshot(
        snapshot_id="snapshot-immutable",
        cycle_id="cycle-immutable",
        decision_id="decision-immutable",
        symbol="BTC/USDT",
        decision_time=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        snapshot_hash="hash-immutable",
        payload={"snapshot_hash": "hash-immutable"},
    )

    row.payload = {"snapshot_hash": "tampered"}
    with pytest.raises(ValueError, match="append-only"):
        db_session.flush()
