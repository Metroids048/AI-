"""Stress diagnostics for validation runs."""

from __future__ import annotations

from shared.models import BacktestReport


def apply_carry_stress_scenarios(report: BacktestReport) -> dict[str, float]:
    """Conservative scenario scores derived from already-net carry metrics."""

    funding_shock_expectancy = report.expectancy - abs(report.expectancy) * 0.35 - 1.0
    liquidity_slippage_expectancy = report.expectancy - float(report.total_cost_bps or 0.0) * 0.25
    exchange_outage_drawdown = report.max_drawdown + 0.05
    return {
        "funding_regime_flip_expectancy": funding_shock_expectancy,
        "liquidity_slippage_expectancy": liquidity_slippage_expectancy,
        "exchange_outage_max_drawdown": exchange_outage_drawdown,
    }


def stress_failures(stress_results: dict[str, float]) -> list[str]:
    failures: list[str] = []
    if stress_results.get("funding_regime_flip_expectancy", 0.0) <= 0:
        failures.append("stress_funding_regime_flip")
    if stress_results.get("liquidity_slippage_expectancy", 0.0) <= 0:
        failures.append("stress_liquidity_slippage")
    if stress_results.get("exchange_outage_max_drawdown", 0.0) > 0.25:
        failures.append("stress_exchange_outage_drawdown")
    return failures
