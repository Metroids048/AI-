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
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.verify_natural_automated_trading_cycle import (
    NaturalCycleEvidence,
    _flag_forbidden_source,
    _preflight,
    observe_natural_cycle,
    write_evidence,
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
