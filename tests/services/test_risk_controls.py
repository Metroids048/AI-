from decimal import Decimal

from services.automated_trading.application.risk_controls import (
    calculate_cost_gate,
    p1_profit_protection,
    shadow_profit_protection,
)


def test_r1_cost_gate_uses_target_relative_net_payoff() -> None:
    result = calculate_cost_gate(
        entry_price=Decimal("100"),
        stop_distance=Decimal("1"),
        take_profit_distance=Decimal("1.5"),
        commission_bps=Decimal("2"),
        slippage_bps=Decimal("0"),
    )
    assert result.cost_r == Decimal("0.02")
    assert result.theoretical_net_payoff > Decimal("1.15")
    assert result.passed
    assert result.reason == "OK"


def test_p1_is_symmetric_and_only_tightens() -> None:
    long = p1_profit_protection(
        direction="long",
        entry_price=Decimal("100"),
        original_stop_price=Decimal("90"),
        mark_price=Decimal("106"),
    )
    short = p1_profit_protection(
        direction="short",
        entry_price=Decimal("100"),
        original_stop_price=Decimal("110"),
        mark_price=Decimal("94"),
    )
    assert long.trigger_r == short.trigger_r == Decimal("0.60")
    assert long.stop_price == Decimal("100.5")
    assert short.stop_price == Decimal("99.5")


def test_p2_p3_are_shadow_only_policies() -> None:
    p2 = shadow_profit_protection(
        policy="P2",
        direction="long",
        entry_price=Decimal("100"),
        original_stop_price=Decimal("90"),
        mark_price=Decimal("112.5"),
    )
    p3 = shadow_profit_protection(
        policy="P3",
        direction="long",
        entry_price=Decimal("100"),
        original_stop_price=Decimal("90"),
        mark_price=Decimal("110"),
    )
    assert p2.lock_r == Decimal("0.50")
    assert p3.lock_r == Decimal("0.10")
