from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.execution.gateway import BinanceUsdtPerpetualGateway
from services.execution.order_context import MarketRulesUnavailable, OrderExecutionContextBuilder
from shared.models import (
    ExchangeSide,
    ExecutionOrderRequest,
    PaperRun,
    PositionSide,
    ProtectionPolicy,
    RuntimeMode,
    TradeAction,
    TradeIntent,
    TradeSide,
)


class PaperCcxtClient:
    id = "binance"

    def __init__(self, *, markets: dict | None = None) -> None:
        self.options = {"defaultMarginMode": "cross"}
        self.markets = {}
        self._markets = markets if markets is not None else self._default_markets()
        self.created_orders: list[dict] = []
        self.leverage_calls: list[tuple[int, str]] = []

    @staticmethod
    def _default_markets() -> dict:
        def market(symbol: str, market_id: str) -> dict:
            return {
                "symbol": symbol,
                "id": market_id,
                "type": "swap",
                "active": True,
                "contractSize": 1,
                "precision": {"price": 2, "amount": 3},
                "limits": {
                    "amount": {"min": 0.001, "max": 1000},
                    "cost": {"min": 20, "max": None},
                },
                "info": {
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "20"},
                    ],
                },
            }

        return {
            "BTC/USDT:USDT": market("BTC/USDT:USDT", "BTCUSDT"),
            "ETH/USDT:USDT": market("ETH/USDT:USDT", "ETHUSDT"),
        }

    def load_markets(self) -> dict:
        self.markets = dict(self._markets)
        return self.markets

    def market(self, symbol: str) -> dict:
        if not self.markets:
            self.load_markets()
        return self.markets[symbol]

    def fetch_balance(self, params=None):  # noqa: ANN001
        return {"total": {"USDT": 10_000}, "free": {"USDT": 10_000}, "info": {"canWithdraw": False}}

    def fetch_position_mode(self) -> dict:
        return {"hedged": False}

    def fetch_positions(self, symbols=None):  # noqa: ANN001
        return []

    def set_leverage(self, leverage, symbol):  # noqa: ANN001
        self.leverage_calls.append((leverage, symbol))
        return {"leverage": leverage, "symbol": symbol}

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):  # noqa: ANN001
        self.created_orders.append(
            {"symbol": symbol, "type": order_type, "side": side, "amount": amount, "price": price, "params": params}
        )
        return {"id": f"paper-{len(self.created_orders)}", "status": "open"}

    def fetch_open_orders(self, symbol=None):  # noqa: ANN001
        return []


def _request(symbol: str, side: TradeSide) -> ExecutionOrderRequest:
    position_side = PositionSide.LONG if side is TradeSide.LONG else PositionSide.SHORT
    exchange_side = ExchangeSide.BUY if side is TradeSide.LONG else ExchangeSide.SELL
    reference = Decimal("60000" if symbol.startswith("BTC") else "3000")
    stop = reference - Decimal("1000") if side is TradeSide.LONG else reference + Decimal("100")
    target = reference + Decimal("2000") if side is TradeSide.LONG else reference - Decimal("200")
    intent = TradeIntent(
        intent_id=f"intent-{symbol}-{side.value}",
        cycle_id="cycle-paper-context",
        decision_id="decision-paper-context",
        strategy_id="strategy-paper-context",
        strategy_version="v1",
        config_snapshot_id="config-paper-context",
        config_hash="sha256:paper-context",
        runtime_mode=RuntimeMode.PAPER,
        symbol=symbol,
        action=TradeAction.OPEN,
        position_side=position_side,
        exchange_side=exchange_side,
        target_quantity=Decimal("0.0109"),
        signal_reference_price=reference,
        protection=ProtectionPolicy(stop_price=stop, take_profit_price=target),
        signal_candle_close_time=datetime(2026, 7, 23, 0, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 23, 0, 0, tzinfo=UTC),
    )
    return ExecutionOrderRequest(
        strategy_id="strategy-paper-context",
        symbol=symbol,
        direction=side,
        entry_context={
            "order_type": "market",
            "requested_notional": float(reference * Decimal("0.0109")),
            "requested_leverage": 40,
        },
        stoploss_plan={"price": float(stop)},
        takeprofit_plan={"price": float(target)},
        trade_intent=intent,
    )


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT"])
@pytest.mark.parametrize("side", [TradeSide.LONG, TradeSide.SHORT])
def test_paper_intent_context_reaches_mock_create_order_once(symbol: str, side: TradeSide) -> None:
    client = PaperCcxtClient()
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=False)
    request = _request(symbol, side)
    paper_run = PaperRun(
        strategy_id="strategy-paper-context",
        candidate_symbols=["BTC/USDT", "ETH/USDT"],
        execution_profile={
            "risk_per_trade": 0.05,
            "max_leverage": 40,
            "max_position_fraction": 0.35,
            "max_total_exposure": 0.90,
        },
    )
    built = OrderExecutionContextBuilder(gateway).build(
        request,
        paper_run=paper_run,
        order_origin="paper_scheduler",
    )

    assert built.market_rules_snapshot is not None
    assert built.market_rules_snapshot.symbol == symbol
    assert built.market_rules_snapshot.exchange_symbol == f"{symbol}:USDT"
    assert built.market_rules_snapshot.tick_size == Decimal("0.10")
    assert built.market_rules_snapshot.step_size == Decimal("0.001")
    assert built.entry_context["fixed_position_settings"] == {
        "risk_per_trade": 0.05,
        "max_position_fraction": 0.35,
        "max_total_exposure": 0.90,
    }
    assert built.entry_context["fixed_leverage_settings"] == {
        "requested_leverage": 40.0,
        "max_leverage": 40,
    }
    result = gateway.submit_order(live_run_id="paper-run-context", order_request=built)

    assert result["gateway_status"] == "acknowledged"
    assert result["gateway_order_id"] == "paper-1"
    assert len(client.created_orders) == 1
    assert client.created_orders[0]["amount"] == 0.01
    assert client.leverage_calls == [(40, f"{symbol}:USDT")]
    assert gateway.reconcile(live_run_id="paper-run-context")["reconciliation_status"] == "ok"


def test_missing_market_metadata_fails_closed_before_create_order() -> None:
    client = PaperCcxtClient(markets={})
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=False)

    with pytest.raises(MarketRulesUnavailable, match="MARKET_RULES_UNAVAILABLE"):
        OrderExecutionContextBuilder(gateway).build(
            _request("BTC/USDT", TradeSide.LONG),
            order_origin="paper_scheduler",
        )
    assert client.created_orders == []


def test_market_metadata_provider_exception_is_reported_as_unavailable() -> None:
    class BrokenMetadataClient(PaperCcxtClient):
        def load_markets(self) -> dict:
            raise RuntimeError("metadata endpoint unavailable")

    client = BrokenMetadataClient(markets={})
    gateway = BinanceUsdtPerpetualGateway(client=client, use_testnet=False)

    with pytest.raises(MarketRulesUnavailable, match="MARKET_RULES_UNAVAILABLE.*metadata endpoint unavailable"):
        OrderExecutionContextBuilder(gateway).build(
            _request("ETH/USDT", TradeSide.SHORT),
            order_origin="paper_scheduler",
        )
    assert client.created_orders == []
