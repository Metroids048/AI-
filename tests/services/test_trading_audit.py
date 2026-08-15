from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.automated_trading.audit.trading_audit import (
    add_excursions,
    build_trade_episodes,
    evaluate_closed_episodes,
    grouped_losses,
)


def _trade(*, trade_id: str, order_id: str, side: str, quantity: str, price: str, realized: str, minute: int) -> dict:
    return {
        "id": trade_id,
        "orderId": order_id,
        "symbol": "BTCUSDT",
        "side": side,
        "qty": quantity,
        "price": price,
        "commission": "1",
        "realizedPnl": realized,
        "time": int((datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=minute)).timestamp() * 1000),
    }


def test_build_trade_episodes_includes_partial_exit_fees_and_funding() -> None:
    episodes = build_trade_episodes(
        [
            _trade(trade_id="1", order_id="entry", side="BUY", quantity="2", price="100", realized="0", minute=0),
            _trade(trade_id="2", order_id="exit-a", side="SELL", quantity="1", price="110", realized="10", minute=1),
            _trade(trade_id="3", order_id="exit-b", side="SELL", quantity="1", price="90", realized="-10", minute=2),
        ],
        funding=[
            {
                "symbol": "BTCUSDT",
                "income": "-0.5",
                "time": int(datetime(2026, 8, 1, 0, 1, tzinfo=UTC).timestamp() * 1000),
            }
        ],
        local_by_trade={("BTC/USDT", "1"): {"strategy": "testnet_sampling_v2"}},
        exit_reason_by_order={"exit-b": "STOP"},
    )

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.status == "CLOSED"
    assert episode.entry_quantity == Decimal("2")
    assert episode.exit_quantity == Decimal("2")
    assert episode.realized_pnl == Decimal("0")
    assert episode.commission == Decimal("3")
    assert episode.funding == Decimal("-0.5")
    assert episode.net_pnl == Decimal("-3.5")
    assert episode.exit_reason == "STOP"
    assert episode.local_context["strategy"] == "testnet_sampling_v2"


def test_build_trade_episodes_handles_a_short_and_reports_actual_metrics() -> None:
    episodes = build_trade_episodes(
        [
            _trade(trade_id="1", order_id="entry", side="SELL", quantity="1", price="100", realized="0", minute=0),
            _trade(trade_id="2", order_id="exit", side="BUY", quantity="1", price="90", realized="10", minute=2),
        ],
        exit_reason_by_order={"exit": "TAKE_PROFIT"},
    )
    add_excursions(episodes[0], [{"high": "102", "low": "85"}])

    assert episodes[0].mfe_pct == Decimal("15.00")
    assert episodes[0].mae_pct == Decimal("-2.00")
    metrics = evaluate_closed_episodes(episodes)
    assert metrics["trades"] == 1
    assert metrics["net_pnl"] == "8"
    assert metrics["profit_factor"] is None
    assert grouped_losses(episodes, "exit_reason") == []
