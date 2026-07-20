from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.data.universe import FIXED_TOP20_SYMBOLS
from services.execution.gateway import (
    BinanceUsdtPerpetualGateway,
    _effective_position_leverage,
    _normalize_binance_symbol,
    _position_numeric,
    configured_gateways,
)
from shared.config import settings
from shared.models import (
    BinanceTestnetOrderView,
    ExchangeSide,
    ExecutionOrderRequest,
    MarketRulesSnapshot,
    PositionSide,
    ProtectionPolicy,
    RuntimeMode,
    TradeAction,
    TradeIntent,
    TradeSide,
)


class StubCcxtClient:
    def __init__(self) -> None:
        self.leverage_calls: list[tuple[int, str]] = []
        self.open_order_symbols: list[str | None] = []
        self.open_algo_orders: list[dict] = []
        self.algo_orders: list[dict] = []
        self.created_orders: list[dict] = []
        self.urls = {
            "test": {"fapiPrivate": "https://testnet.binancefuture.com/fapi/v1"},
            "api": {"fapiPrivate": "https://fapi.binance.com/fapi/v1"},
        }

    def clone(self, value):  # noqa: ANN001
        return dict(value)

    def fetch_time(self, params=None):  # noqa: ANN001
        raise RuntimeError("demo blocked")

    def fetch_balance(self, params=None):  # noqa: ANN001
        assert params == {"type": "future"}
        return {
            "total": {"USDT": 1200.0},
            "free": {"USDT": 1000.0},
            "info": {"totalMarginBalance": "1180.0", "totalUnrealizedProfit": "12.0"},
        }

    def fetch_positions(self):
        return [{"symbol": "BTC/USDT:USDT", "contracts": 1}]

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
        assert symbol == "BTC/USDT:USDT"
        assert order_type == "market"
        assert amount == 0.01
        self.created_orders.append(
            {
                "symbol": symbol,
                "order_type": order_type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params,
            }
        )
        return {"id": "binance-order-1", "status": "open"}

    def fapiPrivatePostAlgoOrder(self, payload):  # noqa: N802, ANN001
        self.algo_orders.append(payload)
        return {"algoId": f"algo-{len(self.algo_orders)}", "status": "NEW"}

    def set_leverage(self, leverage, symbol):  # noqa: ANN001
        self.leverage_calls.append((leverage, symbol))
        return {"leverage": leverage, "symbol": symbol}

    def cancel_order(self, order_id, symbol=None):  # noqa: ANN001
        assert order_id == "binance-order-1"
        return {"id": order_id, "status": "canceled"}

    def fetch_open_orders(self, symbol=None):  # noqa: ANN001
        self.open_order_symbols.append(symbol)
        return []

    def fapiPrivateGetOpenAlgoOrders(self, payload):  # noqa: N802, ANN001
        assert payload == {}
        return list(self.open_algo_orders)

    def fetch_ticker(self, symbol):  # noqa: ANN001
        assert symbol == "BTC/USDT:USDT"
        return {"last": 60_000.0}


def test_binance_gateway_normalizes_platform_contract_aliases() -> None:
    assert _normalize_binance_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert _normalize_binance_symbol("PEPE/USDT") == "1000PEPE/USDT:USDT"


def test_binance_position_numeric_falls_back_to_raw_exchange_info() -> None:
    raw = {"leverage": None, "info": {"leverage": "40", "entryPrice": "63289.10"}}

    assert _position_numeric(raw, "leverage") == 40.0
    assert _position_numeric(raw, "entryPrice", "entry_price") == 63289.10


def test_binance_gateway_reads_leverage_from_position_risk_endpoint() -> None:
    class PositionRiskClient(StubCcxtClient):
        def fapiPrivateV3GetPositionRisk(self, payload):  # noqa: N802, ANN001
            assert payload == {}
            return [{"symbol": "BTCUSDT", "leverage": "40"}]

    gateway = BinanceUsdtPerpetualGateway(client=PositionRiskClient(), use_testnet=True)

    assert gateway._fetch_position_leverages() == {"BTCUSDT": 40.0}


def test_binance_effective_leverage_falls_back_to_notional_margin_ratio() -> None:
    assert _effective_position_leverage({"notional": "1600", "initialMargin": "40"}) == 40.0


def test_binance_order_view_exposes_utc_timestamp_for_millisecond_update_time() -> None:
    order = BinanceTestnetOrderView(
        order_id="1",
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        status="FILLED",
        quantity=0.01,
        update_time=1_784_378_462_000,
    )

    assert order.updated_at is not None
    assert order.updated_at.isoformat() == "2026-07-18T12:41:02+00:00"


def test_configured_gateways_keeps_disabled_gateway_available_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")

    gateway = configured_gateways()[0]

    assert gateway.capability.gateway_name == "binance_usdt_perpetual"
    with pytest.raises(ValueError, match="credentials are not configured"):
        gateway.sync_account(live_run_id="live-run-1")


def test_binance_gateway_maps_account_order_cancel_and_reconcile() -> None:
    client = StubCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    snapshot = gateway.sync_account(live_run_id="live-run-1")
    submitted = gateway.submit_order(
        live_run_id="live-run-1",
        order_request=ExecutionOrderRequest(
            strategy_id="strategy-1",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            stoploss_plan={"price": 59000},
            takeprofit_plan={"price": 62000},
            entry_context={"order_type": "market", "quantity": 0.01},
        ),
    )
    leverage = gateway.set_leverage(symbol="BTC/USDT", leverage=2)
    cancelled = gateway.cancel_order(gateway_order_id="binance-order-1")
    reconciled = gateway.reconcile(live_run_id="live-run-1")

    assert snapshot.wallet_balance == 1200.0
    assert snapshot.open_position_count == 1
    assert client.urls["api"]["fapiPrivate"] == "https://testnet.binancefuture.com/fapi/v1"
    assert submitted["gateway_order_id"] == "binance-order-1"
    assert client.created_orders[0]["side"] == "buy"
    # The stub reports an unfilled order.  Brackets must wait for a confirmed
    # fill so an entry limit cannot reserve ReduceOnly exit capacity.
    assert submitted["gateway_status"] == "acknowledged"
    assert submitted["protection_order_refs"] == []
    assert client.algo_orders == []
    assert leverage["gateway_status"] == "acknowledged"
    assert client.leverage_calls == [(2, "BTC/USDT:USDT")]
    assert cancelled["gateway_status"] == "cancelled"
    assert reconciled["reconciliation_status"] == "ok"


def test_binance_gateway_uses_normalized_hedge_close_matrix() -> None:
    class HedgeClient(StubCcxtClient):
        def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
            self.created_orders.append(
                {"symbol": symbol, "order_type": order_type, "side": side, "amount": amount, "params": params}
            )
            return {"id": "hedge-close-1", "status": "closed"}

    now = datetime(2026, 7, 20, 7, 0, tzinfo=UTC)
    intent = TradeIntent(
        intent_id="intent-hedge-close",
        cycle_id="cycle-1",
        decision_id="decision-1",
        strategy_id="strategy-1",
        strategy_version="v1",
        config_snapshot_id="config-1",
        config_hash="sha256:config",
        runtime_mode=RuntimeMode.PAPER,
        symbol="BTC/USDT:USDT",
        action=TradeAction.CLOSE,
        position_side=PositionSide.SHORT,
        exchange_side=ExchangeSide.BUY,
        target_quantity=Decimal("0.0039"),
        signal_reference_price=Decimal("60000"),
        protection=ProtectionPolicy(stop_price=Decimal("61000")),
        signal_candle_close_time=now,
        created_at=now,
    )
    rules = MarketRulesSnapshot(
        rules_snapshot_id="rules-1",
        symbol="BTC/USDT:USDT",
        market_status="TRADING",
        position_mode="HEDGE",
        margin_mode="CROSS",
        leverage=Decimal("3"),
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("20"),
        loaded_at=now,
    )
    client = HedgeClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    gateway.submit_order(
        live_run_id="live-run-1",
        order_request=ExecutionOrderRequest(
            strategy_id="strategy-1",
            symbol="BTC/USDT:USDT",
            direction=TradeSide.SHORT,
            entry_context={"order_type": "market"},
            trade_intent=intent,
            market_rules_snapshot=rules,
            confirmed_position_quantity=Decimal("0.0024"),
        ),
    )

    created = client.created_orders[0]
    assert created["side"] == "buy"
    assert created["amount"] == 0.002
    assert created["params"]["positionSide"] == "SHORT"
    assert "reduceOnly" not in created["params"]


def test_binance_gateway_closes_filled_entry_when_protection_submit_fails() -> None:
    class ProtectionFailureClient(StubCcxtClient):
        def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
            self.created_orders.append(
                {
                    "symbol": symbol,
                    "order_type": order_type,
                    "side": side,
                    "amount": amount,
                    "price": price,
                    "params": params,
                }
            )
            return {"id": f"order-{len(self.created_orders)}", "status": "closed"}

        def fapiPrivatePostAlgoOrder(self, payload):  # noqa: N802, ANN001
            raise RuntimeError("algo endpoint rejected protection")

    client = ProtectionFailureClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)
    request = ExecutionOrderRequest(
        strategy_id="strategy-1",
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context={
            "order_type": "market",
            "quantity": 0.01,
            "reference_price": 60_000,
            "requested_notional": 600,
        },
        stoploss_plan={"price": 59_000},
    )

    with pytest.raises(ValueError, match="protection_order_submit_failed.*reduce_only_close"):
        gateway.submit_order(live_run_id="live-run-1", order_request=request)

    assert len(client.created_orders) == 2
    assert client.created_orders[1]["side"] == "sell"
    assert client.created_orders[1]["params"]["reduceOnly"] is True


def test_binance_gateway_arms_protection_only_after_a_confirmed_entry_fill() -> None:
    class FilledClient(StubCcxtClient):
        def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
            self.created_orders.append({"symbol": symbol, "order_type": order_type, "side": side, "params": params})
            return {"id": "filled-entry", "status": "closed"}

    client = FilledClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)
    result = gateway.submit_order(
        live_run_id="paper-run",
        order_request=ExecutionOrderRequest(
            strategy_id="strategy-1",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            entry_context={"order_type": "limit", "quantity": 0.01, "limit_price": 60000},
            stoploss_plan={"price": 59000},
            takeprofit_plan={"price": 62000},
        ),
    )

    assert result["gateway_status"] == "filled"
    assert len(result["protection_order_refs"]) == 2
    assert [item["type"] for item in client.algo_orders] == ["STOP_MARKET"]
    assert client.created_orders[1]["order_type"] == "limit"
    assert client.created_orders[1]["params"]["reduceOnly"] is True


def test_binance_gateway_exposes_acceptance_adapter_without_bypassing_submit_order() -> None:
    class EmptyAccountClient(StubCcxtClient):
        def fetch_positions(self):
            return []

        def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
            self.created_orders.append(
                {
                    "symbol": symbol,
                    "order_type": order_type,
                    "side": side,
                    "amount": amount,
                    "price": price,
                    "params": params,
                }
            )
            return {"id": f"binance-order-{len(self.created_orders)}", "status": "closed"}

    client = EmptyAccountClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    assert gateway.preflight() == {"open_orders": [], "open_positions": []}
    assert client.open_order_symbols == [_normalize_binance_symbol(symbol) for symbol in FIXED_TOP20_SYMBOLS]
    assert gateway.account_equity() == 1180.0
    assert gateway.fetch_last_price("BTC/USDT") == 60_000.0

    opened = gateway.submit_acceptance_order(
        symbol="BTC/USDT",
        side="buy",
        requested_notional=600,
        reference_price=60_000,
        reduce_only=False,
        stoploss_price=58_500,
        takeprofit_price=63_000,
        idempotency_key="accept-btc-open",
    )
    closed = gateway.submit_acceptance_order(
        symbol="BTC/USDT",
        side="sell",
        requested_notional=600,
        reference_price=60_000,
        reduce_only=True,
        stoploss_price=None,
        takeprofit_price=None,
        idempotency_key="accept-btc-close",
    )

    assert opened["gateway_status"] == "filled"
    assert opened["quantity"] == 0.01
    assert opened["reduce_only"] is False
    assert client.algo_orders[0]["type"] == "STOP_MARKET"
    assert client.created_orders[1]["order_type"] == "limit"
    assert client.created_orders[1]["price"] == 63_000
    assert closed["reduce_only"] is True
    assert client.created_orders[2]["params"]["reduceOnly"] is True
    assert gateway.final_state() == {"open_orders": [], "open_positions": []}


def test_binance_gateway_acceptance_preflight_includes_open_algo_orders() -> None:
    class EmptyAccountClient(StubCcxtClient):
        def fetch_positions(self):
            return []

    client = EmptyAccountClient()
    client.open_algo_orders = [
        {
            "algoId": "stale-take-profit",
            "symbol": "ETHUSDT",
            "orderType": "TAKE_PROFIT_MARKET",
            "reduceOnly": True,
        }
    ]
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    state = gateway.preflight()

    assert state["open_orders"] == client.open_algo_orders
    assert gateway.reconcile(live_run_id="paper-testnet:algo-scan")["open_order_count"] == 1


def test_binance_gateway_exposes_perp_carry_leg_adapter() -> None:
    class CarryClient(StubCcxtClient):
        def fetch_positions(self):
            return []

        def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
            self.created_orders.append({"side": side, "amount": amount, "params": params})
            return {"id": f"carry-{len(self.created_orders)}", "status": "closed"}

    client = CarryClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    entered = gateway.submit_carry_order(
        symbol="BTC/USDT:USDT",
        side="sell",
        notional_usdt=600,
        quantity=0.01,
        reduce_only=False,
        idempotency_key="carry-perp-entry",
    )
    closed = gateway.submit_carry_order(
        symbol="BTC/USDT:USDT",
        side="buy",
        notional_usdt=600,
        quantity=0.01,
        reduce_only=True,
        idempotency_key="carry-perp-exit",
    )

    assert entered["side"] == "sell"
    assert client.created_orders[0]["side"] == "sell"
    assert closed["reduce_only"] is True
    assert client.created_orders[1]["side"] == "buy"
    assert client.created_orders[1]["params"]["reduceOnly"] is True


def test_binance_gateway_close_only_inverts_position_side() -> None:
    client = StubCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    submitted = gateway.submit_order(
        live_run_id="live-run-1",
        order_request=ExecutionOrderRequest(
            strategy_id="strategy-1",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            entry_context={"order_type": "market", "quantity": 0.01, "close_only_mode": True},
        ),
    )

    assert submitted["gateway_order_id"] == "binance-order-1"
    assert client.created_orders[0]["side"] == "sell"
    assert client.created_orders[0]["params"]["reduceOnly"] is True


def test_binance_gateway_rejects_far_protection_price_before_entry(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gateway_protection_max_distance_bps", 800)
    client = StubCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    with pytest.raises(ValueError, match="protection_price_too_far"):
        gateway.submit_order(
            live_run_id="live-run-1",
            order_request=ExecutionOrderRequest(
                strategy_id="strategy-1",
                symbol="BTC/USDT",
                direction=TradeSide.LONG,
                stoploss_plan={"price": 60000},
                takeprofit_plan={"price": 70000},
                entry_context={"order_type": "market", "quantity": 0.001, "reference_price": 61675.14},
            ),
        )

    assert client.created_orders == []
    assert client.algo_orders == []


def test_binance_gateway_propagates_stable_client_order_id_from_idempotency_key() -> None:
    client = StubCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)
    request = ExecutionOrderRequest(
        strategy_id="strategy-1",
        symbol="BTC/USDT",
        direction=TradeSide.LONG,
        entry_context={"order_type": "market", "quantity": 0.01},
        idempotency_key="same-logical-order",
    )

    gateway.submit_order(live_run_id="live-run-1", order_request=request)
    gateway.submit_order(live_run_id="live-run-1", order_request=request)

    first_id = client.created_orders[0]["params"].get("newClientOrderId")
    second_id = client.created_orders[1]["params"].get("newClientOrderId")
    assert first_id == second_id
    assert first_id.startswith("aq-")
    assert len(first_id) <= 36


def test_binance_gateway_rejects_below_min_notional_before_exchange_submit() -> None:
    client = StubCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=True)

    with pytest.raises(ValueError, match="below_min_notional"):
        gateway.submit_order(
            live_run_id="live-run-1",
            order_request=ExecutionOrderRequest(
                strategy_id="strategy-1",
                symbol="BTC/USDT",
                direction=TradeSide.LONG,
                stoploss_plan={"price": 95},
                takeprofit_plan={"price": 105},
                entry_context={
                    "order_type": "market",
                    "quantity": 0.1,
                    "reference_price": 100,
                    "requested_notional": 10,
                    "min_notional_usdt": 50,
                },
            ),
        )

    assert client.created_orders == []


def test_binance_gateway_rejects_withdrawal_enabled_api_key() -> None:
    class WithdrawalEnabledClient(StubCcxtClient):
        def fetch_balance(self, params=None):  # noqa: ANN001
            payload = super().fetch_balance(params=params)
            payload["info"]["canWithdraw"] = True
            return payload

    with pytest.raises(ValueError, match="withdrawal permission"):
        BinanceUsdtPerpetualGateway(client=WithdrawalEnabledClient(), use_testnet=True)


def test_binance_gateway_recovers_timeout_by_querying_client_order_id() -> None:
    class TimeoutAfterAcceptClient(StubCcxtClient):
        def create_order(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.created_orders.append({"params": kwargs.get("params") or args[-1]})
            raise TimeoutError("exchange response timed out")

        def fapiPrivateGetOrder(self, payload):  # noqa: N802, ANN001
            assert payload["origClientOrderId"].startswith("aq-")
            return {"orderId": "recovered-order", "status": "FILLED"}

    gateway = BinanceUsdtPerpetualGateway(client=TimeoutAfterAcceptClient(), use_testnet=True)
    result = gateway.submit_order(
        live_run_id="live-run-1",
        order_request=ExecutionOrderRequest(
            strategy_id="strategy-1",
            symbol="BTC/USDT",
            direction=TradeSide.LONG,
            entry_context={"order_type": "market", "quantity": 0.01},
            idempotency_key="recover-after-timeout",
        ),
    )

    assert result["gateway_order_id"] == "recovered-order"
    assert result["gateway_status"] == "filled"


def test_binance_gateway_reconcile_survives_open_orders_warning() -> None:
    class OpenOrdersWarnClient(StubCcxtClient):
        def fetch_open_orders(self, symbol=None):  # noqa: ANN001
            raise RuntimeError("fetchOpenOrders() WARNING: specify a symbol")

        def fetch_positions(self):
            return []

    gateway = BinanceUsdtPerpetualGateway(client=OpenOrdersWarnClient(), use_testnet=True)
    snapshot = gateway.reconcile(live_run_id="paper-testnet:ghost-clear")

    assert snapshot["open_positions"] == []
    assert snapshot["open_order_count"] == 0
    assert snapshot["reconciliation_status"] == "warning"
    assert any(note.startswith("open_orders_scan_failed:") for note in snapshot["notes"])
