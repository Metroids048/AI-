"""Generate paper orders from validated strategies before gatekeeper review."""

from __future__ import annotations

from decimal import Decimal

from services.data import DataRepository
from services.data.market import MarketQueryService
from services.execution.decision_pipeline import DecisionPipeline, DecisionPipelineResult
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    NotificationRepository,
    ReviewRepository,
    StrategyRepository,
)
from shared.config import settings
from shared.models import (
    DecisionVetoResult,
    ExecutionOrderRequest,
    ExecutionRiskState,
    PaperRun,
    PaperRunStepRequest,
    PositionSnapshot,
    StrategyContract,
    TradeSide,
)

from .risk_tiers import resolve_asset_risk_tier


class PaperSignalGenerator:
    """Create a candidate paper order; final approval always belongs to gatekeeper."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        execution_repo: ExecutionRepository | None = None,
        agent_repo: AgentTaskRepository | None = None,
        strategy_repo: StrategyRepository | None = None,
        review_repo: ReviewRepository | None = None,
        notification_repo: NotificationRepository | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.decision_pipeline = DecisionPipeline(
            data_repo=data_repo,
            execution_repo=execution_repo,
            agent_repo=agent_repo,
            strategy_repo=strategy_repo,
            review_repo=review_repo,
            notification_repo=notification_repo,
        )

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
        decision = self._decision_for_strategy(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            request=request,
            paper_run=paper_run,
        )
        reference_price = decision.reference_price
        direction = decision.direction or TradeSide.LONG
        stoploss, takeprofit = self._risk_prices(
            reference_price=reference_price,
            direction=direction,
            strategy=strategy,
            atr=decision.atr,
        )
        requested_leverage = self._requested_leverage(strategy=strategy, paper_run=paper_run, symbol=symbol)
        requested_notional = self._requested_notional(
            strategy=strategy,
            paper_run=paper_run,
            symbol=symbol,
            requested_leverage=requested_leverage,
            confidence_multiplier=decision.confidence_multiplier,
            reference_price=reference_price,
            stoploss_price=stoploss,
        )
        risk_state = self._build_risk_state(
            paper_run=paper_run,
            positions=positions,
            symbol=symbol,
            requested_notional=requested_notional,
            requested_leverage=requested_leverage,
        )
        veto_result = decision.veto_result
        if not decision.should_trade and veto_result is None:
            pipeline_status = str(decision.trace.get("pipeline_status", ""))
            carry_admission_skip = pipeline_status.startswith("funding_arbitrage_rejected")
            if not carry_admission_skip:
                veto_result = DecisionVetoResult(
                    veto=True,
                    veto_reason=f"decision pipeline skipped order: {decision.reason}",
                    checked_at=decision.bar_time,
                )
        return ExecutionOrderRequest(
            strategy_id=paper_run.strategy_id,
            version_id=paper_run.version_id,
            symbol=symbol,
            direction=direction,
            risk_profile_id=paper_run.execution_profile.get("risk_profile_id"),
            entry_context={
                "timeframe": timeframe,
                "paper_signal_source": "paper_signal_generator",
                "paper_strategy_source": strategy.source,
                "reference_price": str(reference_price),
                "requested_notional": requested_notional,
                "requested_leverage": requested_leverage,
                "min_notional_usdt": float(strategy.rules.position_rules.get("min_notional_usdt", 50.0)),
                "decision_pipeline": decision.trace,
                "decision_reason": decision.reason,
                "decision_bar_time": decision.bar_time.isoformat() if decision.bar_time else None,
                "paper_order_should_trade": decision.should_trade,
            },
            stoploss_plan={"price": float(stoploss), "basis": "strategy_rule_or_atr_required_stop"},
            takeprofit_plan={"price": float(takeprofit), "basis": "strategy_rule_or_atr_takeprofit"},
            signal_ensemble_id=decision.ensemble.ensemble_id if decision.ensemble is not None else None,
            meta_label_id=decision.meta_label.meta_label_id if decision.meta_label is not None else None,
            veto_result=veto_result,
            validation_backtest_run_id=paper_run.gate_decision_ref,
            paper_run_id=paper_run.paper_run_id,
            risk_state=risk_state,
            idempotency_key=request.idempotency_key,
        )

    def _decision_for_strategy(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        request: PaperRunStepRequest,
        paper_run: PaperRun | None = None,
    ) -> DecisionPipelineResult:
        rules = strategy.rules
        if _is_carry_strategy(rules=rules, paper_run=paper_run):
            return self._carry_decision(
                strategy=strategy,
                symbol=symbol,
                timeframe=timeframe,
                request=request,
                paper_run=paper_run,
            )

        return self.decision_pipeline.evaluate(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            enable_decision_veto=request.enable_decision_veto,
            relaxed_signals=settings.paper_runtime_relaxed_signals,
        )

    def _carry_decision(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        request: PaperRunStepRequest,
        paper_run: PaperRun | None,
    ) -> DecisionPipelineResult:
        rules = strategy.rules
        perp_symbol = request.perp_symbol or f"{symbol}:USDT"
        entry_rules = rules.entry_rules
        fee_bps = float(entry_rules.get("fee_bps", 8.0))
        slippage_bps = float(entry_rules.get("slippage_bps", 6.0))
        threshold_bps = float(entry_rules.get("funding_threshold_bps", 0.5))
        requires_positive = bool(entry_rules.get("requires_positive_funding", True))
        min_net_edge_bps = float(entry_rules.get("min_estimated_net_edge_bps", fee_bps + slippage_bps))

        market_service = MarketQueryService(self.data_repo)
        signal = market_service.get_funding_arbitrage_signal(
            symbol=symbol,
            perp_symbol=perp_symbol,
            timeframe=timeframe,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )

        rejection_reasons = list(signal.rejection_reasons)
        funding_bps = signal.funding_bps or 0.0
        estimated_net = signal.estimated_net_edge_bps

        if funding_bps < threshold_bps:
            rejection_reasons.append("below_funding_threshold")
        if estimated_net is None or estimated_net < min_net_edge_bps:
            rejection_reasons.append("below_min_estimated_net_edge")

        should_trade = (
            signal.should_enter_paper
            and funding_bps >= threshold_bps
            and estimated_net is not None
            and estimated_net >= min_net_edge_bps
        )
        if requires_positive and (signal.funding_rate is None or signal.funding_rate <= 0):
            should_trade = False
            if "requires_positive_funding" not in rejection_reasons:
                rejection_reasons.append("requires_positive_funding")

        if should_trade and signal.funding_rate is not None and signal.funding_rate > 0:
            direction = TradeSide.SHORT
        elif should_trade and signal.funding_rate is not None and signal.funding_rate < 0 and not requires_positive:
            direction = TradeSide.LONG
        else:
            direction = TradeSide.SHORT

        bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=timeframe)
        reference_price = Decimal("0") if bar is None else bar.close
        pipeline_status = "funding_arbitrage_admitted" if should_trade else "funding_arbitrage_rejected"
        return DecisionPipelineResult(
            direction=direction,
            should_trade=should_trade,
            reason="funding_arbitrage_admitted" if should_trade else "funding_arbitrage_rejected",
            reference_price=reference_price,
            bar_time=bar.timestamp if bar else None,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=None,
            volatility_context={"regime": "funding_arbitrage"},
            trace={
                "pipeline_status": pipeline_status,
                "strategy_lane": "carry",
                "funding_rate": str(signal.funding_rate) if signal.funding_rate is not None else None,
                "funding_bps": funding_bps,
                "estimated_net_edge_bps": estimated_net,
                "funding_threshold_bps": threshold_bps,
                "min_estimated_net_edge_bps": min_net_edge_bps,
                "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
                "perp_symbol": perp_symbol,
                "basis_bps": signal.basis_bps,
            },
        )

    def _risk_prices(
        self,
        *,
        reference_price: Decimal,
        direction: TradeSide,
        strategy: StrategyContract,
        atr: float | None,
    ) -> tuple[Decimal, Decimal]:
        if reference_price <= 0:
            reference_price = Decimal("1")
        stop_distance = self._stop_distance(reference_price=reference_price, strategy=strategy, atr=atr)
        take_distance = self._take_distance(
            stop_distance=stop_distance,
            strategy=strategy,
            reference_price=reference_price,
        )
        if direction == TradeSide.LONG:
            return max(reference_price - stop_distance, Decimal("0.00000001")), reference_price + take_distance
        return reference_price + stop_distance, max(reference_price - take_distance, Decimal("0.00000001"))

    @staticmethod
    def _stop_distance(
        *,
        reference_price: Decimal,
        strategy: StrategyContract,
        atr: float | None,
    ) -> Decimal:
        rules = strategy.rules.stoploss_rules
        if "fixed_bps" in rules:
            return reference_price * Decimal(str(rules["fixed_bps"])) / Decimal("10000")
        if "basis_bps" in rules:
            return reference_price * Decimal(str(rules["basis_bps"])) / Decimal("10000")
        atr_multiple = Decimal(str(rules.get("atr_multiple", 1.5)))
        if atr is not None and atr > 0:
            return Decimal(str(atr)) * atr_multiple
        if "max_net_loss_bps" in rules:
            return reference_price * Decimal(str(rules["max_net_loss_bps"])) / Decimal("10000")
        return reference_price * Decimal("0.015")

    @staticmethod
    def _take_distance(
        *,
        stop_distance: Decimal,
        strategy: StrategyContract,
        reference_price: Decimal,
    ) -> Decimal:
        rules = strategy.rules.takeprofit_rules
        if "fixed_bps" in rules:
            return reference_price * Decimal(str(rules["fixed_bps"])) / Decimal("10000")
        if "min_net_profit_bps" in rules:
            return reference_price * Decimal(str(rules["min_net_profit_bps"])) / Decimal("10000")
        reward = Decimal(str(rules.get("risk_reward", 2.0)))
        return stop_distance * reward

    @staticmethod
    def _requested_leverage(*, strategy: StrategyContract, paper_run: PaperRun, symbol: str) -> float:
        tier = resolve_asset_risk_tier(symbol, paper_run.execution_profile.get("asset_risk_tiers"))
        if paper_run.execution_profile.get("asset_risk_tiers"):
            return tier.leverage
        position_rules = strategy.rules.position_rules
        leverage = position_rules.get("max_leverage") or paper_run.execution_profile.get("max_leverage") or 1.0
        return float(leverage)

    @staticmethod
    def _requested_notional(
        *,
        strategy: StrategyContract,
        paper_run: PaperRun,
        symbol: str,
        requested_leverage: float,
        confidence_multiplier: float = 1.0,
        reference_price: Decimal | None = None,
        stoploss_price: Decimal | None = None,
    ) -> float:
        position_rules = strategy.rules.position_rules
        if "notional_usdt" in position_rules:
            return float(position_rules["notional_usdt"]) * max(confidence_multiplier, 0.0)
        if "order_notional_usdt" in position_rules:
            return float(position_rules["order_notional_usdt"]) * max(confidence_multiplier, 0.0)
        account_equity = float(
            paper_run.paper_metrics_summary.get("account_equity")
            or paper_run.execution_profile.get("account_equity")
            or 10_000.0
        )
        if "risk_per_trade" in position_rules:
            risk_budget = account_equity * float(position_rules["risk_per_trade"])
            if reference_price is not None and stoploss_price is not None:
                stop_distance = abs(float(reference_price - stoploss_price))
                if stop_distance > 0 and float(reference_price) > 0:
                    quantity = risk_budget / stop_distance
                    volatility_sized_notional = quantity * float(reference_price)
                    tier = resolve_asset_risk_tier(symbol, paper_run.execution_profile.get("asset_risk_tiers"))
                    max_fraction = (
                        tier.max_position_fraction
                        if paper_run.execution_profile.get("asset_risk_tiers")
                        else float(position_rules.get("max_position_fraction", 0.05))
                    )
                    base = min(volatility_sized_notional, account_equity * max_fraction)
                    return base * max(confidence_multiplier, 0.0)
            base = float(risk_budget * max(requested_leverage, 1.0))
            return base * max(confidence_multiplier, 0.0)
        return min(account_equity * 0.05, 1_000.0) * max(confidence_multiplier, 0.0)

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


def _is_carry_strategy(*, rules, paper_run: PaperRun | None) -> bool:  # noqa: ANN001
    if paper_run is not None:
        lane = paper_run.execution_profile.get("strategy_lane")
        if lane == "carry":
            return True
        if lane == "directional":
            return False
    return "funding_threshold_bps" in rules.entry_rules
