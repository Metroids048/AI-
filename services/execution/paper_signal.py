"""Generate paper orders from validated strategies before gatekeeper review."""

from __future__ import annotations

from decimal import Decimal

from services.data import DataRepository
from shared.models import (
    ExecutionOrderRequest,
    ExecutionRiskState,
    PaperRun,
    PaperRunStepRequest,
    PositionSnapshot,
    StrategyContract,
    TradeSide,
)


class PaperSignalGenerator:
    """Create a candidate paper order; final approval always belongs to gatekeeper."""

    def __init__(self, *, data_repo: DataRepository) -> None:
        self.data_repo = data_repo

    def generate_order(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        request: PaperRunStepRequest,
        positions: list[PositionSnapshot],
    ) -> ExecutionOrderRequest:
        symbol = request.symbol or (paper_run.symbol_scope[0] if paper_run.symbol_scope else "BTC/USDT")
        timeframe = request.timeframe or str(strategy.timeframe)
        bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=timeframe)
        reference_price = Decimal("0") if bar is None else bar.close
        direction = self._direction_for_strategy(strategy=strategy, symbol=symbol, request=request)
        stoploss, takeprofit = self._risk_prices(reference_price=reference_price, direction=direction)
        requested_leverage = self._requested_leverage(strategy=strategy, paper_run=paper_run)
        requested_notional = self._requested_notional(
            strategy=strategy,
            paper_run=paper_run,
            requested_leverage=requested_leverage,
        )
        risk_state = self._build_risk_state(
            paper_run=paper_run,
            positions=positions,
            symbol=symbol,
            requested_notional=requested_notional,
            requested_leverage=requested_leverage,
        )
        return ExecutionOrderRequest(
            strategy_id=paper_run.strategy_id,
            version_id=paper_run.version_id,
            symbol=symbol,
            direction=direction,
            entry_context={
                "timeframe": timeframe,
                "paper_signal_source": "paper_signal_generator",
                "paper_strategy_source": strategy.source,
                "reference_price": str(reference_price),
                "requested_notional": requested_notional,
                "requested_leverage": requested_leverage,
            },
            stoploss_plan={"price": float(stoploss), "basis": "paper_generated_required_stop"},
            takeprofit_plan={"price": float(takeprofit), "basis": "paper_generated_takeprofit"},
            validation_backtest_run_id=paper_run.gate_decision_ref,
            paper_run_id=paper_run.paper_run_id,
            risk_state=risk_state,
            idempotency_key=request.idempotency_key,
        )

    def _direction_for_strategy(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        request: PaperRunStepRequest,
    ) -> TradeSide:
        rules = strategy.rules
        if "funding_threshold_bps" in rules.entry_rules:
            perp_symbol = request.perp_symbol or f"{symbol}:USDT"
            latest_funding = self.data_repo.get_latest_market_extras(symbol=perp_symbol)
            if latest_funding is not None and latest_funding.funding_rate is not None:
                return TradeSide.SHORT if latest_funding.funding_rate > 0 else TradeSide.LONG
            return TradeSide.SHORT

        bars = self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=request.timeframe, limit=2)
        if len(bars) >= 2 and bars[-1].close < bars[-2].close:
            return TradeSide.SHORT
        return TradeSide.LONG

    def _risk_prices(self, *, reference_price: Decimal, direction: TradeSide) -> tuple[Decimal, Decimal]:
        if reference_price <= 0:
            reference_price = Decimal("1")
        if direction == TradeSide.LONG:
            return reference_price * Decimal("0.98"), reference_price * Decimal("1.03")
        return reference_price * Decimal("1.02"), reference_price * Decimal("0.97")

    @staticmethod
    def _requested_leverage(*, strategy: StrategyContract, paper_run: PaperRun) -> float:
        position_rules = strategy.rules.position_rules
        leverage = position_rules.get("max_leverage") or paper_run.execution_profile.get("max_leverage") or 1.0
        return float(leverage)

    @staticmethod
    def _requested_notional(
        *,
        strategy: StrategyContract,
        paper_run: PaperRun,
        requested_leverage: float,
    ) -> float:
        position_rules = strategy.rules.position_rules
        if "notional_usdt" in position_rules:
            return float(position_rules["notional_usdt"])
        if "order_notional_usdt" in position_rules:
            return float(position_rules["order_notional_usdt"])
        account_equity = float(
            paper_run.paper_metrics_summary.get("account_equity")
            or paper_run.execution_profile.get("account_equity")
            or 10_000.0
        )
        if "risk_per_trade" in position_rules:
            return float(account_equity * float(position_rules["risk_per_trade"]) * max(requested_leverage, 1.0))
        return min(account_equity * 0.05, 1_000.0)

    @staticmethod
    def _build_risk_state(
        *,
        paper_run: PaperRun,
        positions: list[PositionSnapshot],
        symbol: str,
        requested_notional: float,
        requested_leverage: float,
    ) -> ExecutionRiskState:
        account_equity = float(
            paper_run.paper_metrics_summary.get("account_equity")
            or paper_run.execution_profile.get("account_equity")
            or 10_000.0
        )
        equity_peak = float(
            paper_run.paper_metrics_summary.get("equity_peak")
            or paper_run.execution_profile.get("equity_peak")
            or account_equity
        )
        total_notional = sum(abs(position.quantity * position.mark_price) for position in positions)
        symbol_notional = sum(
            abs(position.quantity * position.mark_price) for position in positions if position.symbol == symbol
        )
        denominator = account_equity if account_equity > 0 else 1.0
        return ExecutionRiskState(
            account_equity=account_equity,
            equity_peak=max(equity_peak, account_equity),
            daily_realized_pnl=float(paper_run.paper_metrics_summary.get("daily_realized_pnl", 0.0)),
            weekly_realized_pnl=float(paper_run.paper_metrics_summary.get("weekly_realized_pnl", 0.0)),
            consecutive_losses=int(paper_run.paper_metrics_summary.get("consecutive_losses", 0)),
            api_failures_window=int(paper_run.paper_metrics_summary.get("api_failures_window", 0)),
            open_positions=len(positions),
            symbol_exposure=float(symbol_notional / denominator),
            total_exposure=float(total_notional / denominator),
            requested_notional=float(requested_notional),
            requested_leverage=float(requested_leverage),
        )
