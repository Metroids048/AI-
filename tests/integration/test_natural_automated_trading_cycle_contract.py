"""Task 17: natural Scheduler E2E contract tests (plan 16.5 / Gate 17).

The valuable tests here are the ones that prove Gate 17 CANNOT be passed by
accident. Gate 17 is the single gate that authorises the claim "Binance Testnet
自然自动开平单链路已打通", so its evaluator must reject:

- missing any link in the cycle -> decision -> candidate -> intent -> order chain
- a non-flat final exchange position
- a local position that is not CLOSED
- reconciliation that is not HEALTHY
- any acceptance / manual / synthetic shortcut

The real-network observation itself is gated behind V2_NATURAL_E2E_ENABLED.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.verify_natural_automated_trading_cycle import (
    NaturalCycleEvidence,
    _flag_forbidden_source,
    _poll_v2_state,
    _preflight,
    observe_natural_cycle,
    write_evidence,
)
from services.automated_trading.infrastructure.models import (
    Base,
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionEvent,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
    V2ReconciliationSnapshot,
)

E2E_ENABLED = os.getenv("V2_NATURAL_E2E_ENABLED", "false").lower() == "true"
requires_e2e = pytest.mark.skipif(
    not E2E_ENABLED,
    reason="V2_NATURAL_E2E_ENABLED is not true; natural E2E observation skipped",
)


def _complete_evidence() -> NaturalCycleEvidence:
    """An evidence bundle that satisfies every Gate 17 requirement."""
    return NaturalCycleEvidence(
        symbol="BTC/USDT",
        cycle_id="cycle-1",
        decision_id="decision-1",
        candidate_id="candidate-1",
        intent_id="intent-1",
        strategy_id="testnet_sampling_v2",
        lane="TESTNET_SAMPLING",
        entry_exchange_order_id="ex-entry-1",
        entry_trade_ids=["trade-1"],
        entry_avg_fill_price="101000",
        position_group_id="pos-1",
        stop_exchange_order_id="ex-stop-1",
        take_profit_exchange_order_id="ex-tp-1",
        exit_trigger="TAKE_PROFIT",
        exit_exchange_order_id="ex-exit-1",
        exit_trade_ids=["trade-2"],
        final_exchange_position_qty="0",
        final_local_position_state="CLOSED",
        final_reconciliation_status="HEALTHY",
    )


# ---------------------------------------------------------------------------
# Gate 17 evaluator strictness
# ---------------------------------------------------------------------------


class TestGate17Strictness:
    def test_complete_evidence_passes(self) -> None:
        assert _complete_evidence().gate17_passed is True

    def test_empty_evidence_fails(self) -> None:
        assert NaturalCycleEvidence().gate17_passed is False

    @pytest.mark.parametrize(
        "field_name",
        [
            "cycle_id",
            "decision_id",
            "candidate_id",
            "intent_id",
            "entry_exchange_order_id",
            "position_group_id",
            "stop_exchange_order_id",
            "exit_trigger",
            "exit_exchange_order_id",
        ],
    )
    def test_missing_any_required_id_fails(self, field_name: str) -> None:
        """Every link in the evidence chain is load-bearing."""
        evidence = _complete_evidence()
        setattr(evidence, field_name, None)
        assert evidence.gate17_passed is False
        assert field_name in evidence.missing_requirements()

    def test_missing_entry_trade_ids_fails(self) -> None:
        evidence = _complete_evidence()
        evidence.entry_trade_ids = []
        assert evidence.gate17_passed is False

    def test_missing_exit_trade_ids_fails(self) -> None:
        evidence = _complete_evidence()
        evidence.exit_trade_ids = []
        assert evidence.gate17_passed is False

    def test_nonzero_final_exchange_position_fails(self) -> None:
        """A leftover open position invalidates the whole run."""
        evidence = _complete_evidence()
        evidence.final_exchange_position_qty = "0.001"
        assert evidence.gate17_passed is False

    def test_missing_final_exchange_position_fails(self) -> None:
        evidence = _complete_evidence()
        evidence.final_exchange_position_qty = None
        assert evidence.gate17_passed is False

    def test_local_position_not_closed_fails(self) -> None:
        evidence = _complete_evidence()
        evidence.final_local_position_state = "PROTECTED"
        assert evidence.gate17_passed is False

    def test_reconciliation_not_healthy_fails(self) -> None:
        evidence = _complete_evidence()
        evidence.final_reconciliation_status = "DEGRADED"
        assert evidence.gate17_passed is False

    def test_acceptance_shortcut_invalidates(self) -> None:
        evidence = _complete_evidence()
        evidence.used_acceptance_shortcut = True
        assert evidence.gate17_passed is False

    def test_manual_intervention_invalidates(self) -> None:
        evidence = _complete_evidence()
        evidence.used_manual_intervention = True
        assert evidence.gate17_passed is False

    def test_synthetic_fill_invalidates(self) -> None:
        evidence = _complete_evidence()
        evidence.used_synthetic_fill = True
        assert evidence.gate17_passed is False


# ---------------------------------------------------------------------------
# Forbidden-source detection
# ---------------------------------------------------------------------------


class TestForbiddenSourceDetection:
    def test_acceptance_strategy_flagged(self) -> None:
        evidence = _complete_evidence()
        _flag_forbidden_source(evidence, "testnet_acceptance_roundtrip")
        assert evidence.used_acceptance_shortcut is True
        assert evidence.gate17_passed is False

    def test_manual_lane_flagged(self) -> None:
        evidence = _complete_evidence()
        _flag_forbidden_source(evidence, "manual_operator_entry")
        assert evidence.used_manual_intervention is True

    def test_synthetic_fill_flagged(self) -> None:
        evidence = _complete_evidence()
        _flag_forbidden_source(evidence, "synthetic_local_fill")
        assert evidence.used_synthetic_fill is True

    def test_fake_adapter_flagged(self) -> None:
        evidence = _complete_evidence()
        _flag_forbidden_source(evidence, "fake_binance_adapter")
        assert evidence.used_synthetic_fill is True

    def test_canary_flagged_as_acceptance(self) -> None:
        evidence = _complete_evidence()
        _flag_forbidden_source(evidence, "canary_order")
        assert evidence.used_acceptance_shortcut is True

    def test_legitimate_sampling_strategy_not_flagged(self) -> None:
        evidence = _complete_evidence()
        _flag_forbidden_source(evidence, "testnet_sampling_v2", "TESTNET_SAMPLING")
        assert evidence.used_acceptance_shortcut is False
        assert evidence.used_manual_intervention is False
        assert evidence.used_synthetic_fill is False
        assert evidence.gate17_passed is True

    def test_none_values_ignored(self) -> None:
        evidence = _complete_evidence()
        _flag_forbidden_source(evidence, None, None)
        assert evidence.gate17_passed is True


# ---------------------------------------------------------------------------
# Preflight and proof typing
# ---------------------------------------------------------------------------


class TestPreflightAndProofType:
    def test_refuses_without_explicit_enable(self) -> None:
        with patch.dict(os.environ, {"V2_NATURAL_E2E_ENABLED": "false"}):
            ok, detail = _preflight()
        assert ok is False
        assert "V2_NATURAL_E2E_ENABLED" in detail

    def test_refuses_when_engine_not_v2_active(self) -> None:
        """Task 17 is meaningless unless V2 is the sole writer."""
        with (
            patch.dict(os.environ, {"V2_NATURAL_E2E_ENABLED": "true"}),
            patch("shared.config.settings") as mock_settings,
        ):
            mock_settings.binance_use_testnet = True
            mock_settings.live_trading_enabled = False
            mock_settings.automated_trading_engine = "v2_shadow"
            ok, detail = _preflight()
        assert ok is False
        assert "v2_active" in detail

    def test_refuses_on_mainnet_config(self) -> None:
        with (
            patch.dict(os.environ, {"V2_NATURAL_E2E_ENABLED": "true"}),
            patch("shared.config.settings") as mock_settings,
        ):
            mock_settings.binance_use_testnet = False
            mock_settings.live_trading_enabled = False
            mock_settings.automated_trading_engine = "v2_active"
            ok, detail = _preflight()
        assert ok is False
        assert "mainnet" in detail.lower()

    def test_observation_stops_at_failed_preflight(self) -> None:
        with patch.dict(os.environ, {"V2_NATURAL_E2E_ENABLED": "false"}):
            evidence = observe_natural_cycle(None, timeout_minutes=1, poll_seconds=1)
        assert evidence.gate17_passed is False
        assert evidence.observed_cycles == 0

    def test_proof_type_is_natural_scheduler_testnet(self) -> None:
        assert NaturalCycleEvidence().proof_type == "NATURAL_SCHEDULER_TESTNET"

    def test_natural_strategy_flag_is_true(self) -> None:
        """Unlike Task 16, this script does claim the natural loop — when it passes."""
        assert NaturalCycleEvidence().natural_strategy is True

    def test_evidence_serialises_without_secrets(self, tmp_path: Path) -> None:
        evidence = _complete_evidence()
        with patch("scripts.verify_natural_automated_trading_cycle.EVIDENCE_DIR", tmp_path):
            path = write_evidence(evidence)
        serialised = json.dumps(json.loads(path.read_text(encoding="utf-8"))).lower()
        for secret_key in ("api_key", "api_secret", "secret"):
            assert secret_key not in serialised

    def test_poll_includes_closed_position_and_reconstructs_exchange_facts(self, tmp_path: Path, monkeypatch) -> None:
        """Gate 17 must retain a CLOSED position long enough to prove the full chain."""
        engine = create_engine(f"sqlite:///{tmp_path / 'natural.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(UTC)
        with factory() as session:
            session.add(
                V2ExecutionCycle(
                    cycle_id="cycle-closed",
                    symbol="BTC/USDT",
                    timeframe="15m",
                    bar_timestamp=now,
                    execution_mode="BINANCE_TESTNET",
                    fencing_token="fence-closed",
                )
            )
            session.add(
                V2ExecutionDecision(
                    decision_id="decision-closed",
                    cycle_id="cycle-closed",
                    candidate_key="testnet_sampling_v2",
                    payload={},
                )
            )
            session.add(
                V2ExecutionIntent(
                    intent_id="intent-entry-closed",
                    cycle_id="cycle-closed",
                    decision_id="decision-closed",
                    symbol="BTC/USDT",
                    direction="long",
                    candidate_key="testnet_sampling_v2",
                    candidate_type="SAMPLING",
                    execution_mode="BINANCE_TESTNET",
                    decision_bar_timestamp=now,
                    state="FILLED",
                )
            )
            session.add(
                V2ExchangeOrder(
                    order_record_id="order-entry-closed",
                    intent_id="intent-entry-closed",
                    client_order_id="client-entry-closed",
                    exchange_order_id="exchange-entry-closed",
                    quantity=1,
                    leverage=1,
                    average_fill_price=100,
                    filled_quantity=1,
                    trade_ids={"trade_ids": ["trade-entry-closed"]},
                    created_at=now,
                )
            )
            session.add(
                V2ExchangeFill(
                    fill_id="fill-entry-closed",
                    intent_id="intent-entry-closed",
                    exchange_order_record_id="order-entry-closed",
                    account_id="binance_testnet",
                    exchange_order_id="exchange-entry-closed",
                    trade_id="trade-entry-closed",
                    symbol="BTC/USDT",
                    side="BUY",
                    reduce_only=False,
                    filled_quantity=1,
                    fill_price=100,
                    commission=0,
                    commission_asset="USDT",
                    exchange_event_time=now,
                    received_at=now,
                    raw_hash="hash-entry-closed",
                )
            )
            session.add(
                V2ManagedPosition(
                    position_id="position-closed",
                    intent_id="intent-entry-closed",
                    order_record_id="order-entry-closed",
                    symbol="BTC/USDT",
                    direction="long",
                    execution_mode="BINANCE_TESTNET",
                    quantity=1,
                    entry_price=100,
                    entry_fee=0,
                    state="CLOSED",
                    projected_at=now,
                    closed_at=now,
                )
            )
            session.add(
                V2ProtectionRecord(
                    protection_id="protection-closed",
                    position_id="position-closed",
                    stop_loss_price=99,
                    take_profit_price=102,
                    stop_client_order_id="client-stop-closed",
                    tp_client_order_id="client-tp-closed",
                    stop_exchange_order_id="exchange-stop-closed",
                    tp_exchange_order_id="exchange-tp-closed",
                    state="PROTECTION_FILLED",
                    created_at=now,
                )
            )
            session.add(
                V2ExecutionIntent(
                    intent_id="intent-exit-closed",
                    cycle_id="cycle-closed",
                    symbol="BTC/USDT",
                    direction="long",
                    candidate_key="exit:position-closed:TAKE_PROFIT",
                    candidate_type="SAMPLING",
                    execution_mode="BINANCE_TESTNET",
                    decision_bar_timestamp=now,
                    state="FILLED",
                )
            )
            session.add(
                V2ExchangeOrder(
                    order_record_id="order-exit-closed",
                    intent_id="intent-exit-closed",
                    client_order_id="client-exit-closed",
                    exchange_order_id="exchange-exit-closed",
                    quantity=1,
                    leverage=1,
                    average_fill_price=102,
                    filled_quantity=1,
                    trade_ids={"trade_ids": ["trade-exit-closed"]},
                    created_at=now,
                )
            )
            session.add(
                V2ExchangeFill(
                    fill_id="fill-exit-closed",
                    intent_id="intent-exit-closed",
                    exchange_order_record_id="order-exit-closed",
                    account_id="binance_testnet",
                    exchange_order_id="exchange-exit-closed",
                    trade_id="trade-exit-closed",
                    symbol="BTC/USDT",
                    side="SELL",
                    reduce_only=True,
                    filled_quantity=1,
                    fill_price=102,
                    commission=0,
                    commission_asset="USDT",
                    exchange_event_time=now,
                    received_at=now,
                    raw_hash="hash-exit-closed",
                )
            )
            session.add(
                V2ExecutionEvent(
                    event_id="event-closed",
                    aggregate_id="position-closed",
                    aggregate_type="POSITION",
                    event_type="PositionClosed",
                    event_payload={"reason": "TAKE_PROFIT"},
                    occurred_at=now,
                )
            )
            session.add(
                V2ReconciliationSnapshot(
                    snapshot_id="snapshot-closed",
                    cycle_id="cycle-closed",
                    execution_mode="BINANCE_TESTNET",
                    exchange_positions={},
                    exchange_open_orders={},
                    local_positions={},
                    discrepancies={},
                    status="HEALTHY",
                    captured_at=now,
                )
            )
            session.commit()

        monkeypatch.setattr("services.database.get_session_factory", lambda: factory)
        state = _poll_v2_state()
        position = state["positions"][0]
        assert position.state == "CLOSED"
        assert position.entry_trade_ids == ["trade-entry-closed"]
        assert position.stop_exchange_order_id == "exchange-stop-closed"
        assert position.exchange_exit_order_id == "exchange-exit-closed"
        assert position.exit_trade_ids == ["trade-exit-closed"]
        assert position.exit_trigger == "TAKE_PROFIT"
        assert state["latest_reconciliation"].status == "HEALTHY"
        engine.dispose()


# ---------------------------------------------------------------------------
# Real observation (skipped by default)
# ---------------------------------------------------------------------------


@requires_e2e
@pytest.mark.natural_e2e
class TestRealNaturalCycle:
    """Observes the real Scheduler. Requires V2_NATURAL_E2E_ENABLED=true."""

    def test_natural_lifecycle_observed(self) -> None:
        evidence = observe_natural_cycle(None, timeout_minutes=180, poll_seconds=30)
        assert evidence.gate17_passed, evidence.missing_requirements()
