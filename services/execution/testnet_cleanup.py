"""Idempotent pre-acceptance cleanup for the Binance Testnet/Simulation account.

This module provides ``testnet_account_cleanup`` which cancels all open orders
and closes all open positions on the paper exchange before an acceptance run.
It is strictly guarded: if the environment cannot be confirmed as Testnet/
Simulation, the function raises ``ValueError`` immediately (fail-closed).
"""

from __future__ import annotations

from typing import Any, Protocol

from shared.models import ExecutionOrderRequest, TradeSide


class CleanupGateway(Protocol):
    """Minimal gateway interface required for account cleanup."""

    def preflight(self) -> dict[str, list[Any]]: ...

    def fetch_last_price(self, symbol: str) -> float: ...

    def cancel_protection_order(self, *, symbol: str, gateway_order_id: str) -> None: ...

    def submit_order(
        self,
        *,
        live_run_id: str,
        order_request: ExecutionOrderRequest,
    ) -> dict[str, Any]: ...


def testnet_account_cleanup(
    gateway: CleanupGateway,
    *,
    idempotency_key: str = "pre-acceptance-cleanup",
    app_settings: Any = None,
) -> dict[str, Any]:
    """Idempotent cleanup of Binance Testnet/Simulation account state.

    Behaviour
    ---------
    - Account already clean (zero positions and zero open orders): returns
      immediately with ``skipped=True``; no exchange requests are sent.
    - Account dirty: cancels every open order, then closes every open position
      via ReduceOnly market orders.  Re-reads account state after cleanup and
      raises ``RuntimeError`` if the account is still not clean.
    - Any individual cancel or close failure causes the function to collect the
      error and then raise ``RuntimeError`` at the end (fail-closed; never
      silently continues).

    Safety guard
    ------------
    Raises ``ValueError`` immediately if the settings cannot confirm that the
    process is running against a Testnet/Simulation environment
    (``BINANCE_USE_TESTNET=true`` AND ``LIVE_TRADING_ENABLED=false``).  This
    prevents accidental invocation against a mainnet account.

    Parameters
    ----------
    gateway:
        A gateway object satisfying ``CleanupGateway`` — in practice a
        ``BinanceUsdtPerpetualGateway`` initialised with ``use_testnet=True``.
    idempotency_key:
        Prefix for order idempotency keys submitted during cleanup.
    app_settings:
        Override the global ``shared.config.settings`` object (used in tests).

    Returns
    -------
    dict with keys:
        skipped (bool)         – True when account was already clean.
        cancelled_orders (list[str]) – IDs of orders successfully cancelled.
        closed_positions (list[str]) – Symbols of positions successfully closed.
        cancel_errors (list[str])    – Empty list (errors raise before return).
        close_errors (list[str])     – Empty list (errors raise before return).

    Raises
    ------
    ValueError
        If the environment is not confirmed as Testnet/Simulation.
    RuntimeError
        If any cancel or close step fails, or if the account is not clean after
        cleanup.
    """
    if app_settings is None:
        from shared.config import settings as _global_settings

        app_settings = _global_settings

    binance_use_testnet = getattr(app_settings, "binance_use_testnet", False)
    live_trading_enabled = getattr(app_settings, "live_trading_enabled", True)

    if not binance_use_testnet or live_trading_enabled:
        raise ValueError(
            "testnet_account_cleanup refused: "
            "BINANCE_USE_TESTNET must be True and LIVE_TRADING_ENABLED must be False. "
            "Cleanup is only permitted on Testnet/Simulation environments to prevent "
            "accidental mainnet order submission."
        )

    state = gateway.preflight()
    open_orders: list[Any] = list(state.get("open_orders") or [])
    open_positions: list[Any] = list(state.get("open_positions") or [])

    if not open_orders and not open_positions:
        return {
            "skipped": True,
            "cancelled_orders": [],
            "closed_positions": [],
            "cancel_errors": [],
            "close_errors": [],
        }

    print(
        f"[testnet_cleanup] account not clean: "
        f"{len(open_positions)} position(s), {len(open_orders)} open order(s) — starting cleanup"
    )

    cancelled_orders: list[str] = []
    cancel_errors: list[str] = []

    # ------------------------------------------------------------------ #
    # Step 1: Cancel all open orders (regular + algo).                    #
    # Algo orders carry an ``algoId`` field; regular CCXT orders use      #
    # ``id``.  ``cancel_protection_order`` handles both via fallback.     #
    # ------------------------------------------------------------------ #
    for idx, order in enumerate(open_orders):
        algo_id = str(order.get("algoId") or "")
        order_id = str(order.get("id") or order.get("orderId") or "")
        raw_symbol = str(order.get("symbol") or "")
        effective_id = algo_id if algo_id else order_id
        if not effective_id:
            cancel_errors.append(
                f"open_order[{idx}] has no identifiable id "
                f"(symbol={raw_symbol!r}); cannot cancel"
            )
            continue
        try:
            gateway.cancel_protection_order(symbol=raw_symbol, gateway_order_id=effective_id)
            cancelled_orders.append(effective_id)
            print(f"[testnet_cleanup] cancelled order {effective_id} on {raw_symbol}")
        except Exception as exc:  # noqa: BLE001
            cancel_errors.append(f"cancel order {effective_id} on {raw_symbol!r}: {exc}")

    if cancel_errors:
        detail = "; ".join(cancel_errors)
        raise RuntimeError(
            f"testnet_account_cleanup: order cancellation failed — {detail}"
        )

    # ------------------------------------------------------------------ #
    # Step 2: Close all open positions via ReduceOnly market orders.      #
    # Direction must match the open side so that ``submit_order`` emits   #
    # the correct opposing side (e.g. LONG + reduce_only → "sell").       #
    # ------------------------------------------------------------------ #
    closed_positions: list[str] = []
    close_errors: list[str] = []

    for idx, position in enumerate(open_positions):
        raw_symbol = str(position.get("symbol") or "")
        contracts = abs(float(position.get("contracts") or 0.0))
        position_side = str(position.get("side") or "long").lower()

        if not raw_symbol or contracts <= 0:
            close_errors.append(
                f"open_position[{idx}] has no usable symbol or contracts "
                f"(symbol={raw_symbol!r}, contracts={contracts}); skipping"
            )
            continue

        direction = TradeSide.LONG if position_side == "long" else TradeSide.SHORT

        try:
            reference_price = gateway.fetch_last_price(raw_symbol)
            gateway.submit_order(
                live_run_id="testnet-pre-acceptance-cleanup",
                order_request=ExecutionOrderRequest(
                    strategy_id="testnet_cleanup",
                    symbol=raw_symbol,
                    direction=direction,
                    entry_context={
                        "order_type": "market",
                        "quantity": contracts,
                        "reference_price": reference_price,
                        "requested_notional": contracts * reference_price,
                        "close_only_mode": True,
                        "reduce_only": True,
                    },
                    idempotency_key=f"{idempotency_key}-close-{idx}",
                ),
            )
            closed_positions.append(raw_symbol)
            print(f"[testnet_cleanup] closed {position_side} position on {raw_symbol} ({contracts} contracts)")
        except Exception as exc:  # noqa: BLE001
            close_errors.append(f"close {position_side} {raw_symbol!r} ({contracts} contracts): {exc}")

    if close_errors:
        detail = "; ".join(close_errors)
        raise RuntimeError(
            f"testnet_account_cleanup: position close failed — {detail}"
        )

    # ------------------------------------------------------------------ #
    # Step 3: Confirm the account is now clean.                           #
    # ------------------------------------------------------------------ #
    final_state = gateway.preflight()
    residual_orders: list[Any] = list(final_state.get("open_orders") or [])
    residual_positions: list[Any] = list(final_state.get("open_positions") or [])

    if residual_orders or residual_positions:
        raise RuntimeError(
            f"testnet_account_cleanup: cleanup completed but account is still not clean — "
            f"{len(residual_positions)} position(s) and {len(residual_orders)} open order(s) remain. "
            "Inspect the Testnet/Simulation account manually before retrying."
        )

    print(
        f"[testnet_cleanup] account is now clean "
        f"(cancelled {len(cancelled_orders)} order(s), closed {len(closed_positions)} position(s))"
    )

    return {
        "skipped": False,
        "cancelled_orders": cancelled_orders,
        "closed_positions": closed_positions,
        "cancel_errors": [],
        "close_errors": [],
    }
