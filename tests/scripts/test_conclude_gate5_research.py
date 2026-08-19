from scripts.conclude_gate5_research import development_failures


def test_development_failure_requires_both_symbol_eligibility_and_complete_costs() -> None:
    result = {
        "portfolio": {
            "net_expectancy": 0.001,
            "profit_factor": 1.4,
            "max_drawdown": 0.1,
            "promotion_observations_complete": False,
        },
        "symbols": {
            "BTC/USDT": {"net_expectancy": -0.001, "profit_factor": 0.9},
            "ETH/USDT": {"net_expectancy": 0.001, "profit_factor": 1.4},
        },
    }

    failures = development_failures(result)

    assert "cost_observations_incomplete" in failures
    assert "BTC/USDT:not_independently_eligible" in failures
