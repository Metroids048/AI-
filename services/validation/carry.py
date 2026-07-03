"""Carry backtest service for the first funding-rate settlement slice."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from shared.models import BacktestEngine, BacktestReport, BacktestRun, GateDecision, OHLCVBar, StrategyContract

from .costs import estimate_round_trip_cost_bps
from .metrics import (
    annualized_sharpe,
    deflated_sharpe,
    expectancy,
    max_drawdown_from_pnls,
    profit_factor,
    sharpe_confidence,
    win_rate,
)


class CarryBacktestService:
    """Platform-side double-leg reconciliation around a freqtrade-first lane."""

    def run_backtest(
        self,
        *,
        strategy: StrategyContract,
        spot_bars: list[OHLCVBar],
        perp_bars: list[OHLCVBar],
        funding_points: list[dict],
    ) -> BacktestRun:
        threshold_bps = Decimal(str(strategy.rules.entry_rules.get("funding_threshold_bps", 5)))
        hold_hours = int(strategy.rules.exit_rules.get("hold_hours", 8))
        trades: list[Decimal] = []
        returns: list[float] = []
        total_fee_bps = Decimal("0")
        total_slippage_bps = Decimal("0")
        total_funding_bps = Decimal("0")
        maker_fee_bps = Decimal(str(strategy.rules.position_rules.get("maker_fee_bps", 2)))
        taker_fee_bps = Decimal(str(strategy.rules.position_rules.get("taker_fee_bps", 4)))
        min_slippage_bps = Decimal(str(strategy.rules.position_rules.get("min_slippage_bps", 1)))

        for funding in funding_points:
            funding_rate = Decimal(str(funding["funding_rate"]))
            if abs(funding_rate) * Decimal("10000") < threshold_bps:
                continue
            start_time = funding["time"]
            end_time = start_time + timedelta(hours=hold_hours)

            spot_entry = next((bar for bar in spot_bars if bar.timestamp == start_time), None)
            spot_exit = next((bar for bar in spot_bars if bar.timestamp == end_time), None)
            perp_entry = next((bar for bar in perp_bars if bar.timestamp == start_time), None)
            perp_exit = next((bar for bar in perp_bars if bar.timestamp == end_time), None)
            if spot_entry is None or spot_exit is None or perp_entry is None or perp_exit is None:
                continue

            notional = Decimal(str(strategy.rules.position_rules.get("notional_usdt", 1000)))
            spot_return = (spot_exit.close - spot_entry.close) / spot_entry.close
            perp_return = (perp_exit.close - perp_entry.close) / perp_entry.close
            funding_leg = abs(funding_rate)
            if funding_rate > 0:
                gross_return = spot_return - perp_return + funding_leg
            else:
                gross_return = perp_return - spot_return + funding_leg
            cost = estimate_round_trip_cost_bps(
                spot_entry=spot_entry.close,
                spot_exit=spot_exit.close,
                perp_entry=perp_entry.close,
                perp_exit=perp_exit.close,
                funding_rate=funding_rate,
                maker_fee_bps=maker_fee_bps,
                taker_fee_bps=taker_fee_bps,
                min_slippage_bps=min_slippage_bps,
            )
            total_fee_bps += cost.fee_bps
            total_slippage_bps += cost.slippage_bps
            total_funding_bps += cost.funding_bps
            trading_cost_return = (cost.fee_bps + cost.slippage_bps) / Decimal("10000")
            net_return = gross_return - trading_cost_return
            net_pnl = notional * net_return
            trades.append(net_pnl)
            returns.append(float(net_return))

        total_trades = len(trades)
        notional = Decimal(str(strategy.rules.position_rules.get("notional_usdt", 1000)))
        periods_per_year = Decimal("365") * Decimal("24") / Decimal(str(hold_hours))
        sharpe = annualized_sharpe(returns, periods_per_year=float(periods_per_year))
        trials_count = int(strategy.rules.position_rules.get("trials_count", 1))
        dsr = deflated_sharpe(sharpe, returns, trials_count=trials_count)
        dsr_probability = sharpe_confidence(dsr, sample_size=len(returns))
        average_costs = {
            "fee_bps": float(total_fee_bps / total_trades) if total_trades else 0.0,
            "slippage_bps": float(total_slippage_bps / total_trades) if total_trades else 0.0,
            "funding_bps": float(total_funding_bps / total_trades) if total_trades else 0.0,
        }

        metrics = BacktestReport(
            strategy_id=strategy.strategy_id,
            engine=BacktestEngine.FREQTRADE,
            sharpe=sharpe,
            profit_factor=profit_factor(trades),
            max_drawdown=max_drawdown_from_pnls(trades, initial_equity=notional),
            win_rate=win_rate(trades),
            expectancy=expectancy(trades),
            total_trades=total_trades,
            deflated_sharpe=dsr,
            trials_count=trials_count,
            total_cost_bps=average_costs["fee_bps"] + average_costs["slippage_bps"],
            cost_breakdown_bps=average_costs,
        )

        failed_thresholds: list[str] = []
        gate_sharpe = metrics.deflated_sharpe if metrics.deflated_sharpe is not None else metrics.sharpe
        if gate_sharpe < 1.0:
            failed_thresholds.append("min_sharpe")
        if metrics.profit_factor < 1.3:
            failed_thresholds.append("min_profit_factor")
        if metrics.max_drawdown > 0.25:
            failed_thresholds.append("max_drawdown")
        if metrics.expectancy <= 0:
            failed_thresholds.append("min_expectancy")

        has_positive_small_sample = (
            0 < total_trades < 3
            and metrics.expectancy > 0
            and metrics.profit_factor >= 1.3
            and metrics.max_drawdown <= 0.25
        )

        if failed_thresholds and has_positive_small_sample:
            gate = GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="conditional",
                reason="insufficient sample for full validation; stress/OOS still pending",
                failed_thresholds=["insufficient_sample"],
                deflated_sharpe_applied=True,
                thresholds_applied={
                    "min_sharpe": 1.0,
                    "min_profit_factor": 1.3,
                    "max_drawdown": 0.25,
                    "min_expectancy": 0.0,
                    "min_full_validation_trades": 3,
                    "dsr_probability": dsr_probability,
                },
            )
        elif failed_thresholds:
            gate = GateDecision(
                strategy_id=strategy.strategy_id,
                passed=False,
                decision_status="rejected_with_reason",
                reason="; ".join(failed_thresholds),
                failed_thresholds=failed_thresholds,
                deflated_sharpe_applied=False,
                thresholds_applied={
                    "min_sharpe": 1.0,
                    "min_profit_factor": 1.3,
                    "max_drawdown": 0.25,
                    "min_expectancy": 0.0,
                    "dsr_probability": dsr_probability,
                },
            )
        else:
            gate = GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="conditional",
                reason="deflated sharpe applied; stress/OOS still pending",
                deflated_sharpe_applied=True,
                thresholds_applied={
                    "min_sharpe": 1.0,
                    "min_profit_factor": 1.3,
                    "max_drawdown": 0.25,
                    "min_expectancy": 0.0,
                    "dsr_probability": dsr_probability,
                },
            )

        return BacktestRun(
            strategy_id=strategy.strategy_id,
            execution_engine="freqtrade",
            sample_split_plan={
                "train_window": "in-sample",
                "oos_window": "out-of-sample",
            },
            validation_methodology={
                "lane": "carry_research",
                "rule_profile": "settlement_window_driven",
            },
            cost_model_ref="spot hedge reconciliation performed platform-side",
            stress_test_scenarios=[
                "funding_flip",
                "missing_settlement",
                "spread_widening",
            ],
            metrics_summary=metrics,
            run_status="completed",
            eligibility_result=gate,
        )
