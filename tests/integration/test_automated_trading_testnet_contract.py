"""Task 16: Binance Testnet contract tests (plan section 16.4 / Gate 16).

Two layers live here:

1. Unguarded tests that verify the *safety machinery* of the contract script
   (preflight refusal, evidence redaction, proof-type tagging). These always
   run and make no network call.

2. ``@pytest.mark.testnet_contract`` tests that place REAL Testnet orders.
   These are skipped unless V2_TESTNET_CONTRACT_ENABLED=true, per plan 16:
   "默认 CI 跳过真实网络测试".

Gate 16 requires real order ids, real trade ids, protection from the real fill,
a real reduce-only exit, and a final zeroed position — and still forbids
claiming the natural strategy loop works.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.verify_automated_trading_testnet_contract import (
    ContractEvidence,
    ContractStep,
    _preflight,
    run_contract,
    write_evidence,
)

CONTRACT_ENABLED = os.getenv("V2_TESTNET_CONTRACT_ENABLED", "false").lower() == "true"
requires_testnet = pytest.mark.skipif(
    not CONTRACT_ENABLED,
    reason="V2_TESTNET_CONTRACT_ENABLED is not true; real-network contract test skipped",
)


# ---------------------------------------------------------------------------
# Safety machinery (always runs, no network)
# ---------------------------------------------------------------------------


class TestPreflightSafety:
    def test_refuses_without_explicit_enable(self) -> None:
        with patch.dict(os.environ, {"V2_TESTNET_CONTRACT_ENABLED": "false"}):
            ok, detail = _preflight()
        assert ok is False
        assert "V2_TESTNET_CONTRACT_ENABLED" in detail

    def test_refuses_when_testnet_flag_off(self) -> None:
        with (
            patch.dict(os.environ, {"V2_TESTNET_CONTRACT_ENABLED": "true"}),
            patch("shared.config.settings") as mock_settings,
        ):
            mock_settings.binance_use_testnet = False
            mock_settings.live_trading_enabled = False
            mock_settings.binance_api_key = "k"
            mock_settings.binance_api_secret = "s"
            ok, detail = _preflight()
        assert ok is False
        assert "mainnet" in detail.lower()

    def test_refuses_when_live_trading_enabled(self) -> None:
        with (
            patch.dict(os.environ, {"V2_TESTNET_CONTRACT_ENABLED": "true"}),
            patch("shared.config.settings") as mock_settings,
        ):
            mock_settings.binance_use_testnet = True
            mock_settings.live_trading_enabled = True
            mock_settings.binance_api_key = "k"
            mock_settings.binance_api_secret = "s"
            ok, detail = _preflight()
        assert ok is False
        assert "mainnet" in detail.lower()

    def test_refuses_without_credentials(self) -> None:
        with (
            patch.dict(os.environ, {"V2_TESTNET_CONTRACT_ENABLED": "true"}),
            patch("shared.config.settings") as mock_settings,
        ):
            mock_settings.binance_use_testnet = True
            mock_settings.live_trading_enabled = False
            mock_settings.binance_api_key = ""
            mock_settings.binance_api_secret = ""
            ok, detail = _preflight()
        assert ok is False
        assert "credential" in detail.lower()

    def test_run_contract_stops_at_failed_preflight(self) -> None:
        """A refused preflight must not proceed to any adapter call."""
        with patch.dict(os.environ, {"V2_TESTNET_CONTRACT_ENABLED": "false"}):
            evidence = run_contract("BTC/USDT", Decimal("20"))

        assert evidence.overall_passed is False
        assert evidence.network_calls == 0
        assert evidence.real_exchange_orders == 0
        assert evidence.entry_exchange_order_id is None
        assert len(evidence.steps) == 1
        assert evidence.steps[0].name == "preflight"


class TestEvidenceContract:
    def test_proof_type_is_testnet_contract(self) -> None:
        evidence = ContractEvidence(symbol="BTC/USDT")
        assert evidence.proof_type == "TESTNET_CONTRACT"

    def test_natural_strategy_is_false(self) -> None:
        """Gate 16 forbids this script from claiming the natural loop."""
        evidence = ContractEvidence(symbol="BTC/USDT")
        assert evidence.natural_strategy is False

    def test_evidence_written_without_credentials(self, tmp_path: Path) -> None:
        evidence = ContractEvidence(symbol="BTC/USDT")
        evidence.add(ContractStep("preflight", True, "ok"))

        with patch("scripts.verify_automated_trading_testnet_contract.EVIDENCE_DIR", tmp_path):
            path = write_evidence(evidence)

        payload = json.loads(path.read_text(encoding="utf-8"))
        serialised = json.dumps(payload).lower()
        for secret_key in ("api_key", "api_secret", "apikey", "secret"):
            assert secret_key not in serialised

    def test_evidence_records_counters(self, tmp_path: Path) -> None:
        evidence = ContractEvidence(symbol="BTC/USDT")
        evidence.network_calls = 5
        evidence.real_exchange_orders = 3

        with patch("scripts.verify_automated_trading_testnet_contract.EVIDENCE_DIR", tmp_path):
            path = write_evidence(evidence)

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["network_calls"] == 5
        assert payload["real_exchange_orders"] == 3

    def test_add_step_accumulates(self) -> None:
        evidence = ContractEvidence(symbol="BTC/USDT")
        evidence.add(ContractStep("a", True, "ok"))
        evidence.add(ContractStep("b", False, "bad"))
        assert len(evidence.steps) == 2
        assert [s.passed for s in evidence.steps] == [True, False]


# ---------------------------------------------------------------------------
# Real network contract (skipped by default)
# ---------------------------------------------------------------------------


@requires_testnet
@pytest.mark.testnet_contract
class TestRealTestnetContract:
    """Places REAL Binance Testnet orders. Requires explicit authorisation."""

    def test_full_contract_sequence(self) -> None:
        evidence = run_contract("BTC/USDT", Decimal("20"))

        # Gate 16 evidence requirements.
        assert evidence.network_calls > 0, "no network call was made"
        assert evidence.real_exchange_orders > 0, "no real exchange order was placed"
        assert evidence.entry_exchange_order_id, "no real entry order id"
        assert evidence.entry_trade_ids, "no real entry trade id"
        assert evidence.entry_avg_fill_price is not None
        assert Decimal(evidence.entry_avg_fill_price) > 0

        assert evidence.exit_exchange_order_id, "no real reduce-only exit order id"
        assert evidence.exit_trade_ids, "no real exit trade id"

        assert evidence.final_exchange_position_qty is not None
        assert Decimal(evidence.final_exchange_position_qty) == 0, "final exchange position is not flat"

        assert evidence.overall_passed, [s.name for s in evidence.steps if not s.passed]

    def test_contract_does_not_claim_natural_strategy(self) -> None:
        """Even on a full pass, the natural-loop claim stays false (Gate 16)."""
        evidence = run_contract("BTC/USDT", Decimal("20"))
        assert evidence.proof_type == "TESTNET_CONTRACT"
        assert evidence.natural_strategy is False
