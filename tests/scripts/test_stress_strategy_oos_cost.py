import json
from decimal import Decimal

from scripts.stress_strategy_oos_cost import stress


def test_cost_stress_is_monotonic(tmp_path) -> None:
    source = tmp_path / "replay.json"
    output = tmp_path / "stress.json"
    source.write_text(
        json.dumps(
            {
                "results": {
                    "candidate": {
                        "trades": [
                            {"net_return": "0.01", "filled_fraction": "1"},
                            {"net_return": "-0.005", "filled_fraction": "0.85"},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = stress(
        report_path=source,
        output_path=output,
        extra_per_side_bps=(Decimal("0"), Decimal("10")),
    )
    base = Decimal(result["scenarios"]["candidate"]["0"]["expectancy"])
    stressed = Decimal(result["scenarios"]["candidate"]["10"]["expectancy"])
    assert stressed < base


def test_cost_stress_supports_multipliers_over_observed_cost(tmp_path) -> None:
    source = tmp_path / "replay.json"
    output = tmp_path / "stress.json"
    source.write_text(
        json.dumps(
            {
                "results": {
                    "candidate": {
                        "trades": [
                            {"gross_return": "0.02", "net_return": "0.01", "filled_fraction": "1"},
                            {"gross_return": "-0.002", "net_return": "-0.005", "filled_fraction": "1"},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = stress(
        report_path=source,
        output_path=output,
        cost_multipliers=(Decimal("1.0"), Decimal("1.5"), Decimal("2.0")),
    )

    scenarios = result["scenarios"]["candidate"]
    assert set(scenarios) == {"1.0x", "1.5x", "2.0x"}
    assert Decimal(scenarios["1.5x"]["expectancy"]) < Decimal(scenarios["1.0x"]["expectancy"])
    assert result["cost_basis"] == "observed_trade_cost_return"
