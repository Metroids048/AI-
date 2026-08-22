from decimal import Decimal

from services.automated_trading.application.risk_controls import (
    calculate_cost_gate,
    p1_profit_protection,
    resolve_effective_funding_bps,
    shadow_profit_protection,
)


def test_funding_direction_semantics_preserve_raw_sign() -> None:
    assert resolve_effective_funding_bps(raw_funding_bps=Decimal("5"), side="LONG") == Decimal("5")
    assert resolve_effective_funding_bps(raw_funding_bps=Decimal("5"), side="SHORT") == Decimal("-5")
    assert resolve_effective_funding_bps(raw_funding_bps=Decimal("-5"), side="LONG") == Decimal("-5")
    assert resolve_effective_funding_bps(raw_funding_bps=Decimal("-5"), side="SHORT") == Decimal("5")
    assert resolve_effective_funding_bps(raw_funding_bps=Decimal("0"), side="SHORT") == Decimal("0")


def test_cost_gate_consumes_effective_funding_for_long_and_short() -> None:
    long_cost = calculate_cost_gate(
        entry_price=Decimal("100"),
        stop_distance=Decimal("1"),
        take_profit_distance=Decimal("1.5"),
        funding_bps=resolve_effective_funding_bps(raw_funding_bps=Decimal("5"), side="LONG"),
        commission_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    short_benefit = calculate_cost_gate(
        entry_price=Decimal("100"),
        stop_distance=Decimal("1"),
        take_profit_distance=Decimal("1.5"),
        funding_bps=resolve_effective_funding_bps(raw_funding_bps=Decimal("5"), side="SHORT"),
        commission_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    assert long_cost.funding_r == Decimal("0.05")
    assert short_benefit.funding_r == Decimal("-0.05")


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


def test_r2_funding_cost_can_flip_a_borderline_trade() -> None:
    without_funding = calculate_cost_gate(
        entry_price=Decimal("65000"),
        stop_distance=Decimal("650"),
        take_profit_distance=Decimal("975"),
        funding_bps=Decimal("0"),
    )
    with_funding = calculate_cost_gate(
        entry_price=Decimal("65000"),
        stop_distance=Decimal("650"),
        take_profit_distance=Decimal("975"),
        funding_bps=Decimal("5"),
    )

    assert without_funding.passed is True
    assert with_funding.passed is False
    assert with_funding.funding_r > 0


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
