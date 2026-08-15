from pytest import approx

from scripts.run_market_alpha_meta_research import _metrics
from services.strategy_library.market_alpha import (
    AlphaSnapshot,
    MarketBar,
    build_snapshot,
    feature_vector,
    taker_imbalance,
    zscore,
)


def test_taker_imbalance_and_zscore_are_bounded_and_point_in_time() -> None:
    assert taker_imbalance(taker_buy_quote=75, quote_volume=100) == 0.5
    assert zscore(3.0, [1.0, 2.0]) > 0
    assert zscore(1.0, [1.0, 1.0]) == 0.0


def test_snapshot_feature_arms_are_distinct() -> None:
    perp = MarketBar(1, 102.0, 1000.0, 600.0)
    previous_perp = MarketBar(0, 100.0, 1000.0, 500.0)
    spot = MarketBar(1, 101.0, 1000.0, 0.0)
    previous_spot = MarketBar(0, 100.0, 1000.0, 0.0)
    snapshot = build_snapshot(
        symbol="ETH/USDT",
        timestamp=1,
        perp=perp,
        perp_previous=previous_perp,
        spot=spot,
        spot_previous=previous_spot,
        perp_history=(previous_perp,),
        basis_history=(),
        funding_rate=0.0001,
        funding_history=(0.0, 0.00005),
        btc_return_1h=0.01,
        relative_return=0.005,
    )
    assert isinstance(snapshot, AlphaSnapshot)
    assert len(feature_vector(snapshot, "PRICE_ONLY")) < len(feature_vector(snapshot, "ALL_ALPHA"))
    assert snapshot.taker_imbalance > 0


def test_research_metrics_are_time_ordered_and_include_stress_fields() -> None:
    from scripts.run_market_alpha_meta_research import ResearchRow

    rows = (
        ResearchRow(
            "ETH/USDT", "long", "2023-01-01T02:00:00+00:00", "", "", 1.5,
            100, 99, 1, 1, {}, "STOP_FIRST", -1.0, 0.1,
        ),
        ResearchRow(
            "BTC/USDT", "long", "2023-01-01T01:00:00+00:00", "", "", 1.5,
            100, 99, 1, 1, {}, "TP_FIRST", 1.5, 0.1,
        ),
    )
    metrics = _metrics(rows)
    assert metrics["trades"] == 2
    assert metrics["expectancy"] == approx(0.15)
    assert metrics["max_drawdown_r"] == approx(1.1)
    assert metrics["stress_expectancy"] == approx(0.1)
    assert metrics["stress_profit_factor"] > 1.0
