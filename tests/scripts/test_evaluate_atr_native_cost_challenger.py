from decimal import Decimal

from scripts.evaluate_atr_native_cost_challenger import _metrics


def test_cost_stress_reduces_net_r_without_changing_gross_returns() -> None:
    trades = [
        {
            "gross_r": Decimal("1.5"),
            "commission_r": Decimal("0.2"),
            "trigger_to_fill_r": Decimal("-0.1"),
            "direction_hit": True,
        },
        {
            "gross_r": Decimal("-1"),
            "commission_r": Decimal("0.2"),
            "trigger_to_fill_r": Decimal("-0.1"),
            "direction_hit": False,
        },
    ]

    base = _metrics(trades, candidates=2, signals_seen=2, stress_multiplier=Decimal("1"))
    stressed = _metrics(trades, candidates=2, signals_seen=2, stress_multiplier=Decimal("1.5"))

    assert Decimal(stressed["net_r"]) < Decimal(base["net_r"])
    assert base["trades"] == stressed["trades"] == 2
