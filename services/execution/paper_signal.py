"""Generate paper orders from validated strategies before gatekeeper review."""

from __future__ import annotations

from decimal import Decimal

from services.data import DataRepository
from shared.models import ExecutionOrderRequest, PaperRun, PaperRunStepRequest, StrategyContract, TradeSide


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
    ) -> ExecutionOrderRequest:
        symbol = request.symbol or (paper_run.symbol_scope[0] if paper_run.symbol_scope else "BTC/USDT")
        timeframe = request.timeframe or str(strategy.timeframe)
        bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=timeframe)
        reference_price = Decimal("0") if bar is None else bar.close
        direction = self._direction_for_strategy(strategy=strategy, symbol=symbol, request=request)
        stoploss, takeprofit = self._risk_prices(reference_price=reference_price, direction=direction)
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
            },
            stoploss_plan={"price": float(stoploss), "basis": "paper_generated_required_stop"},
            takeprofit_plan={"price": float(takeprofit), "basis": "paper_generated_takeprofit"},
            validation_backtest_run_id=paper_run.gate_decision_ref,
            paper_run_id=paper_run.paper_run_id,
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
