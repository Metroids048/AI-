"""Tests for services.execution.testnet_cleanup.testnet_account_cleanup.

Four scenarios are covered:
  1. Account already clean  → function skips, emits no exchange requests.
  2. Account has positions  → cleanup succeeds, account becomes clean.
  3. Close request fails    → function raises RuntimeError (fail-closed).
  4. Non-testnet settings   → function raises ValueError immediately.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.execution.testnet_cleanup import testnet_account_cleanup
from shared.models import ExecutionOrderRequest

# Prevent pytest from treating the imported service function as a test
# (its name starts with "test", which triggers collection).
testnet_account_cleanup.__test__ = False  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


class _Settings:
    """Lightweight stand-in for shared.config.settings."""

    def __init__(self, *, binance_use_testnet: bool, live_trading_enabled: bool) -> None:
        self.binance_use_testnet = binance_use_testnet
        self.live_trading_enabled = live_trading_enabled


_TESTNET_SETTINGS = _Settings(binance_use_testnet=True, live_trading_enabled=False)
_MAINNET_SETTINGS = _Settings(binance_use_testnet=False, live_trading_enabled=False)
_LIVE_SETTINGS = _Settings(binance_use_testnet=True, live_trading_enabled=True)


class _FakeCleanupGateway:
    """Configurable fake that satisfies CleanupGateway."""

    def __init__(
        self,
        *,
        initial_orders: list[dict[str, Any]] | None = None,
        initial_positions: list[dict[str, Any]] | None = None,
        fail_cancel: bool = False,
        fail_close: bool = False,
        final_dirty: bool = False,
    ) -> None:
        self._preflight_calls = 0
        self._initial_orders: list[dict[str, Any]] = initial_orders or []
        self._initial_positions: list[dict[str, Any]] = initial_positions or []
        self._fail_cancel = fail_cancel
        self._fail_close = fail_close
        self._final_dirty = final_dirty

        self.cancelled_ids: list[str] = []
        self.submitted_orders: list[ExecutionOrderRequest] = []

    def preflight(self) -> dict[str, list[Any]]:
        self._preflight_calls += 1
        # First call returns initial state; subsequent calls simulate post-cleanup state.
        if self._preflight_calls == 1:
            return {
                "open_orders": list(self._initial_orders),
                "open_positions": list(self._initial_positions),
            }
        # Second call (post-cleanup confirmation): return dirty if forced, else clean.
        if self._final_dirty:
            return {
                "open_orders": list(self._initial_orders),
                "open_positions": list(self._initial_positions),
            }
        return {"open_orders": [], "open_positions": []}

    def fetch_last_price(self, symbol: str) -> float:  # noqa: ARG002
        return 50_000.0

    def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None:  # noqa: ARG002
        if self._fail_cancel:
            raise ValueError("simulated cancel failure")
        self.cancelled_ids.append(gateway_order_id)

    def submit_order(
        self,
        *,
        live_run_id: str,  # noqa: ARG002
        order_request: ExecutionOrderRequest,
    ) -> dict[str, Any]:
        if self._fail_close:
            raise ValueError("simulated close failure")
        self.submitted_orders.append(order_request)
        return {"gateway_order_id": "cleanup-order-1", "gateway_status": "filled"}


# --------------------------------------------------------------------------- #
# Scenario 1: account already clean                                            #
# --------------------------------------------------------------------------- #


def test_cleanup_skips_when_account_is_already_clean() -> None:
    gateway = _FakeCleanupGateway()  # no positions, no orders

    result = testnet_account_cleanup(gateway, app_settings=_TESTNET_SETTINGS)

    assert result["skipped"] is True
    assert result["cancelled_orders"] == []
    assert result["closed_positions"] == []
    assert result["cancel_errors"] == []
    assert result["close_errors"] == []
    # Only one preflight call (the initial state check) — no confirmation call needed.
    assert gateway._preflight_calls == 1
    assert gateway.cancelled_ids == []
    assert gateway.submitted_orders == []


# --------------------------------------------------------------------------- #
# Scenario 2: account has positions — cleanup succeeds                        #
# --------------------------------------------------------------------------- #


def test_cleanup_cancels_orders_and_closes_positions_then_confirms_clean() -> None:
    initial_orders = [
        {"id": "order-123", "symbol": "BTC/USDT:USDT", "algoId": ""},
        {"algoId": "algo-456", "symbol": "ETH/USDT:USDT", "id": ""},
    ]
    initial_positions = [
        {"symbol": "BTC/USDT:USDT", "contracts": 0.001, "side": "long"},
        {"symbol": "ETH/USDT:USDT", "contracts": 0.01, "side": "short"},
    ]
    gateway = _FakeCleanupGateway(
        initial_orders=initial_orders,
        initial_positions=initial_positions,
    )

    result = testnet_account_cleanup(gateway, idempotency_key="test-cleanup", app_settings=_TESTNET_SETTINGS)

    assert result["skipped"] is False
    assert result["cancel_errors"] == []
    assert result["close_errors"] == []

    # Both open orders were cancelled.
    assert set(result["cancelled_orders"]) == {"order-123", "algo-456"}
    assert set(gateway.cancelled_ids) == {"order-123", "algo-456"}

    # Both positions were closed via ReduceOnly market orders.
    assert set(result["closed_positions"]) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    assert len(gateway.submitted_orders) == 2

    close_requests = {req.symbol: req for req in gateway.submitted_orders}
    btc_req = close_requests["BTC/USDT:USDT"]
    eth_req = close_requests["ETH/USDT:USDT"]

    # Long BTC position → reduce_only close
    assert btc_req.entry_context["reduce_only"] is True
    assert btc_req.entry_context["close_only_mode"] is True
    assert btc_req.entry_context["order_type"] == "market"

    # Short ETH position → reduce_only close (opposite direction)
    assert eth_req.entry_context["reduce_only"] is True
    assert eth_req.entry_context["close_only_mode"] is True

    # Two preflight calls: initial state check + post-cleanup confirmation.
    assert gateway._preflight_calls == 2


# --------------------------------------------------------------------------- #
# Scenario 3: close request fails — function must raise (fail-closed)         #
# --------------------------------------------------------------------------- #


def test_cleanup_raises_when_position_close_fails() -> None:
    initial_positions = [
        {"symbol": "BTC/USDT:USDT", "contracts": 0.001, "side": "long"},
    ]
    gateway = _FakeCleanupGateway(
        initial_positions=initial_positions,
        fail_close=True,
    )

    with pytest.raises(RuntimeError, match="position close failed"):
        testnet_account_cleanup(gateway, app_settings=_TESTNET_SETTINGS)

    # No confirmation call should have been made — failure is immediate.
    # (preflight_calls == 1: initial state; cleanup raises before confirmation)
    assert gateway._preflight_calls == 1


def test_cleanup_raises_when_order_cancel_fails() -> None:
    initial_orders = [{"id": "order-999", "symbol": "BTC/USDT:USDT"}]
    gateway = _FakeCleanupGateway(
        initial_orders=initial_orders,
        fail_cancel=True,
    )

    with pytest.raises(RuntimeError, match="order cancellation failed"):
        testnet_account_cleanup(gateway, app_settings=_TESTNET_SETTINGS)


def test_cleanup_raises_when_confirmation_still_dirty_after_cleanup() -> None:
    initial_positions = [
        {"symbol": "BTC/USDT:USDT", "contracts": 0.001, "side": "long"},
    ]
    gateway = _FakeCleanupGateway(
        initial_positions=initial_positions,
        final_dirty=True,  # confirmation call will return positions still present
    )

    with pytest.raises(RuntimeError, match="still not clean"):
        testnet_account_cleanup(gateway, app_settings=_TESTNET_SETTINGS)

    # Both preflight calls should have been made.
    assert gateway._preflight_calls == 2


# --------------------------------------------------------------------------- #
# Scenario 4: non-Testnet environment — refuse immediately                    #
# --------------------------------------------------------------------------- #


def test_cleanup_refuses_when_binance_use_testnet_is_false() -> None:
    gateway = _FakeCleanupGateway()

    with pytest.raises(ValueError, match="refused"):
        testnet_account_cleanup(gateway, app_settings=_MAINNET_SETTINGS)

    # No preflight call should have been made.
    assert gateway._preflight_calls == 0
    assert gateway.cancelled_ids == []
    assert gateway.submitted_orders == []


def test_cleanup_refuses_when_live_trading_enabled_is_true() -> None:
    gateway = _FakeCleanupGateway()

    with pytest.raises(ValueError, match="refused"):
        testnet_account_cleanup(gateway, app_settings=_LIVE_SETTINGS)

    assert gateway._preflight_calls == 0


def test_cleanup_error_message_names_both_required_conditions() -> None:
    """Error message must mention BINANCE_USE_TESTNET and LIVE_TRADING_ENABLED."""
    gateway = _FakeCleanupGateway()

    with pytest.raises(ValueError) as exc_info:
        testnet_account_cleanup(gateway, app_settings=_MAINNET_SETTINGS)

    msg = str(exc_info.value)
    assert "BINANCE_USE_TESTNET" in msg
    assert "LIVE_TRADING_ENABLED" in msg
