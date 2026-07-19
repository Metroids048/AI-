"""Generate paper orders from validated strategies before gatekeeper review."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from services.data import DataRepository
from services.data.market import MarketQueryService
from services.data.universe import execution_scope_hash
from services.execution.account_equity import resolve_paper_account_equity
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
    OrderType,
    PaperRun,
    PaperRunStepRequest,
    PositionSnapshot,
    StrategyContract,
    TradeSide,
)

from .portfolio_risk import close_returns, correlation, signed_exposure
from .risk_tiers import resolve_asset_risk_tier

logger = logging.getLogger(__name__)

# Notional below this fraction of account equity is almost certainly a sizing
# misconfiguration (e.g. risk_per_trade * leverage collapsing to near zero)
# rather than an intentional micro-size order, so it's worth a loud warning.
MIN_SANE_NOTIONAL_FRACTION = 0.005

# Default same-side return-correlation coefficient above which a position is
# treated as a "peer" for cluster-exposure risk purposes. Operator-overridable
# via paper_run.execution_profile["correlation_peer_threshold"].
DEFAULT_CORRELATION_PEER_THRESHOLD = 0.70


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
        self.execution_repo = execution_repo
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
        order_type = str(strategy.rules.entry_rules.get("order_type", settings.execution_default_order_type))
        limit_price = self._limit_price(
            reference_price=reference_price,
            direction=direction,
            order_type=order_type,
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
            strategy=strategy,
            positions=positions,
            symbol=symbol,
            direction=direction,
            requested_notional=requested_notional,
            requested_leverage=requested_leverage,
            reference_price=reference_price,
            stoploss_price=stoploss,
        )
        requested_notional = risk_state.requested_notional
        veto_result = decision.veto_result
        if not decision.should_trade and veto_result is None:
            pipeline_status = str(decision.trace.get("pipeline_status", ""))
            carry_admission_skip = pipeline_status.startswith(
                ("funding_arbitrage_rejected", "cross_sectional_carry_rejected")
            )
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
                "runtime_build_id": settings.app_build_id,
                "execution_scope_hash": execution_scope_hash(),
                "paper_signal_source": "paper_signal_generator",
                "paper_strategy_source": strategy.source,
                "reference_price": str(reference_price),
                "order_type": order_type,
                "limit_price": limit_price,
                "entry_limit_expiry_at": (
                    (decision.bar_time + timedelta(minutes=15)).isoformat()
                    if order_type == OrderType.LIMIT and decision.bar_time is not None
                    else None
                ),
                "requested_notional": requested_notional,
                "requested_leverage": requested_leverage,
                "estimated_round_trip_cost_bps": self._round_trip_cost_bps(strategy=strategy, symbol=symbol),
                "runtime_config_version": paper_run.execution_profile.get("runtime_config_version"),
                "max_portfolio_initial_risk_fraction": float(
                    paper_run.execution_profile.get(
                        "max_portfolio_initial_risk_fraction",
                        strategy.rules.position_rules.get("max_portfolio_initial_risk_fraction", 0.05),
                    )
                ),
                "min_notional_usdt": float(
                    paper_run.execution_profile.get(
                        "min_notional_usdt", strategy.rules.position_rules.get("min_notional_usdt", 50.0)
                    )
                ),
                "correlated_peer_count_limit": int(paper_run.execution_profile.get("correlated_peer_count_limit", 2)),
                "correlated_cluster_exposure_limit": float(
                    paper_run.execution_profile.get("correlated_cluster_exposure_limit", 0.35)
                ),
                "net_directional_exposure_limit": float(
                    paper_run.execution_profile.get("net_directional_exposure_limit", 0.40)
                ),
                "decision_pipeline": decision.trace,
                "decision_reason": decision.reason,
                "decision_bar_time": decision.bar_time.isoformat() if decision.bar_time else None,
                "paper_order_should_trade": decision.should_trade,
                "meta_label_win_rate": decision.trace.get("meta_label_win_rate"),
                "meta_label_average_win": decision.trace.get("meta_label_average_win"),
                "meta_label_average_loss": decision.trace.get("meta_label_average_loss"),
                "round_trip_fee_rate": decision.trace.get("round_trip_fee_rate"),
                "round_trip_slippage_rate": decision.trace.get("round_trip_slippage_rate"),
                "candidate_id": decision.trace.get("candidate_id"),
                "rules_hash": decision.trace.get("rules_hash"),
                "edge_stats_source": decision.trace.get("edge_stats_source"),
                "edge_artifact_ref": decision.trace.get("edge_artifact_ref"),
                "validated_edge_required": decision.trace.get("validated_edge_required", False),
                "validated_edge_net_expectancy": decision.trace.get("validated_edge_net_expectancy"),
                "validated_edge_oos_sample_count": decision.trace.get("validated_edge_oos_sample_count"),
                "strategy_lane": decision.trace.get("strategy_lane"),
                "observation_only_mode": decision.trace.get("strategy_lane") == "signal_observation",
                "strategy_performance_eligible": decision.trace.get("strategy_lane")
                not in ("link_verification", "signal_observation"),
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
        if _is_link_verification_strategy(rules=rules, paper_run=paper_run):
            return self._link_verification_decision(
                symbol=symbol,
                timeframe=timeframe,
            )
        if _is_cross_sectional_strategy(rules=rules, paper_run=paper_run):
            return self._cross_sectional_decision(
                strategy=strategy,
                symbol=symbol,
                timeframe=timeframe,
                request=request,
            )
        if _is_carry_strategy(rules=rules, paper_run=paper_run):
            return self._carry_decision(
                strategy=strategy,
                symbol=symbol,
                timeframe=timeframe,
                request=request,
                paper_run=paper_run,
            )

        is_signal_observation = (
            paper_run is not None and paper_run.execution_profile.get("strategy_lane") == "signal_observation"
        )
        decision = self.decision_pipeline.evaluate(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            enable_decision_veto=request.enable_decision_veto,
            relaxed_signals=settings.paper_runtime_relaxed_signals or is_signal_observation,
        )
        if is_signal_observation:
            return replace(
                decision,
                trace={
                    **decision.trace,
                    "strategy_lane": "signal_observation",
                    "edge_stats_source": "raw_bar_proxy_non_authoritative",
                },
            )
        return decision

    def _link_verification_decision(
        self,
        *,
        symbol: str,
        timeframe: str,
    ) -> DecisionPipelineResult:
        """Bypass real signal/ensemble/meta-label evaluation entirely and admit a
        fixed-direction order with a hardcoded favorable edge. This lane exists to
        exercise the order -> stoploss -> takeprofit -> close pipeline itself
        (see AGENTS.md link-verification isolation requirement); it never
        measures signal quality, so it must not depend on ensemble_discarded /
        meta_label_bet_skipped or any real net-edge computation that could
        non-deterministically withhold an order."""
        bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=timeframe)
        reference_price = Decimal("0") if bar is None else bar.close
        return DecisionPipelineResult(
            direction=TradeSide.LONG,
            should_trade=True,
            reason="link_verification_admitted",
            reference_price=reference_price,
            bar_time=bar.timestamp if bar else None,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=None,
            volatility_context={"regime": "link_verification"},
            trace={
                "pipeline_status": "link_verification_admitted",
                "strategy_lane": "link_verification",
                "meta_label_win_rate": 1.0,
                "meta_label_average_win": 1.0,
                "meta_label_average_loss": 0.0,
                "round_trip_fee_rate": 0.0,
                "round_trip_slippage_rate": 0.0,
            },
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

        # Delta-neutral hedge leg: spot position to offset perpetual price risk
        hedge_leg = None
        if should_trade:
            spot_symbol = symbol  # e.g. "BTC/USDT"
            hedge_direction = TradeSide.LONG if direction == TradeSide.SHORT else TradeSide.SHORT
            hedge_leg = {
                "symbol": spot_symbol,
                "direction": str(hedge_direction),
                "order_type": "market",
                "is_spot": True,
                "reason": "delta_neutral_hedge_for_funding_carry",
                "perp_symbol": perp_symbol,
            }

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
                "round_trip_cost_bps": signal.round_trip_cost_bps,
                "estimated_net_edge_bps": estimated_net,
                "funding_threshold_bps": threshold_bps,
                "min_estimated_net_edge_bps": min_net_edge_bps,
                "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
                "perp_symbol": perp_symbol,
                "basis_bps": signal.basis_bps,
                "hedge_leg": hedge_leg,
            },
        )

    def _cross_sectional_decision(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        request: PaperRunStepRequest,
    ) -> DecisionPipelineResult:
        entry_rules = strategy.rules.entry_rules
        fee_bps = float(entry_rules.get("fee_bps", 5.0))
        slippage_bps = float(entry_rules.get("slippage_bps", 3.0))
        min_net_edge_bps = float(entry_rules.get("min_estimated_net_edge_bps", 2 * (fee_bps + slippage_bps)))
        bar = self.data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe=timeframe)
        reference_price = Decimal("0") if bar is None else bar.close

        rank_payload = request.cross_sectional_rank
        rejection_reasons: list[str] = []
        if rank_payload is None:
            rejection_reasons.append("missing_funding_rank_snapshot")
        basket_side = rank_payload.get("basket_side") if rank_payload else None
        funding_rate_bps = float(rank_payload["funding_rate_bps"]) if rank_payload else None
        rank = int(rank_payload["rank"]) if rank_payload else None
        total_ranked = int(rank_payload["total_ranked"]) if rank_payload else None
        if basket_side is None and rank_payload is not None:
            rejection_reasons.append("outside_funding_basket")

        # Funding income (paid every settlement window) is the entire edge; there
        # is no directional price forecast, so the round-trip cost gate only has
        # to clear a much smaller bar than a directional entry -- one taker fill
        # to open, one taker fill to close.
        estimated_net_edge_bps = (
            abs(funding_rate_bps) - 2 * (fee_bps + slippage_bps) if funding_rate_bps is not None else None
        )
        if estimated_net_edge_bps is None or estimated_net_edge_bps < min_net_edge_bps:
            rejection_reasons.append("net_edge_after_cost_negative")

        should_trade = not rejection_reasons
        direction = TradeSide.SHORT if basket_side == "short_candidate" else TradeSide.LONG
        pipeline_status = "cross_sectional_carry_admitted" if should_trade else "cross_sectional_carry_rejected"
        return DecisionPipelineResult(
            direction=direction,
            should_trade=should_trade,
            reason=pipeline_status,
            reference_price=reference_price,
            bar_time=bar.timestamp if bar else None,
            signals=[],
            ensemble=None,
            meta_label=None,
            veto_result=None,
            confidence_multiplier=1.0,
            atr=None,
            volatility_context={"regime": "cross_sectional_funding_carry"},
            trace={
                "pipeline_status": pipeline_status,
                "strategy_lane": "cross_sectional_carry",
                "basket_side": basket_side,
                "funding_rate_bps": funding_rate_bps,
                "rank": rank,
                "total_ranked": total_ranked,
                "estimated_net_edge_bps": estimated_net_edge_bps,
                "min_estimated_net_edge_bps": min_net_edge_bps,
                "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
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
    def _limit_price(*, reference_price: Decimal, direction: TradeSide, order_type: str) -> float | None:
        """Book-unaware limit price: reference_price offset by a configurable slippage buffer.

        No order-book/bid-ask data exists in this platform, so this is intentionally simple —
        it only bounds how far a resting limit order can chase price, not a real quote.
        """

        if order_type != OrderType.LIMIT:
            return None
        # Rest at the signal price.  A marketable offset turns a limit entry
        # into a disguised market order and defeats the no-chasing policy.
        return float(reference_price)

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

    def _requested_notional(
        self,
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
        account_equity, _equity_source = resolve_paper_account_equity(
            paper_run=paper_run,
            execution_repo=self.execution_repo,
        )
        tier = resolve_asset_risk_tier(symbol, paper_run.execution_profile.get("asset_risk_tiers"))
        max_fraction = (
            tier.max_position_fraction
            if paper_run.execution_profile.get("asset_risk_tiers")
            else float(position_rules.get("max_position_fraction", 0.05))
        )
        # Keep sizing under the RiskProfile single-symbol exposure limit so
        # gatekeeper does not reject every open as max_symbol_exposure_exceeded.
        # Historical audit: leverage-sized path produced notional/equity=0.80 while
        # medium profile max_symbol_exposure=0.20 (docs/audits/2026-07-14-exposure-rejection-audit.md).
        # Only apply execution_profile.max_symbol_exposure as an extra ceiling when set;
        # do not fall back to strategy position_rules.max_position_fraction here — that
        # would silently override asset_risk_tiers (see test_asset_risk_tiers).
        profile_cap = paper_run.execution_profile.get("max_symbol_exposure")
        hard_fraction_cap = max(0.01, max_fraction)
        if profile_cap is not None:
            hard_fraction_cap = max(0.01, min(hard_fraction_cap, float(profile_cap)))

        sizing_basis = "fallback_equity_fraction"
        if "notional_usdt" in position_rules:
            sizing_basis = "notional_usdt"
            notional = float(position_rules["notional_usdt"]) * max(confidence_multiplier, 0.0)
        elif "order_notional_usdt" in position_rules:
            sizing_basis = "order_notional_usdt"
            notional = float(position_rules["order_notional_usdt"]) * max(confidence_multiplier, 0.0)
        elif "risk_per_trade" in position_rules:
            risk_per_trade = float(paper_run.execution_profile.get("risk_per_trade", position_rules["risk_per_trade"]))
            risk_budget = account_equity * risk_per_trade
            stop_distance = (
                abs(float(reference_price - stoploss_price))
                if reference_price is not None and stoploss_price is not None
                else 0.0
            )
            if stop_distance > 0 and reference_price is not None and float(reference_price) > 0:
                sizing_basis = "risk_per_trade_volatility_sized"
                quantity = risk_budget / stop_distance
                volatility_sized_notional = quantity * float(reference_price)
                notional = min(volatility_sized_notional, account_equity * hard_fraction_cap) * max(
                    confidence_multiplier, 0.0
                )
            else:
                sizing_basis = "risk_per_trade_leverage_sized"
                uncapped = float(risk_budget * max(requested_leverage, 1.0)) * max(confidence_multiplier, 0.0)
                notional = min(uncapped, account_equity * hard_fraction_cap)
        else:
            notional = min(account_equity * hard_fraction_cap, 1_000.0) * max(confidence_multiplier, 0.0)

        if account_equity > 0 and notional > account_equity * hard_fraction_cap:
            notional = account_equity * hard_fraction_cap
            sizing_basis = f"{sizing_basis}_capped_to_max_position_fraction"

        if account_equity > 0 and (notional / account_equity) < MIN_SANE_NOTIONAL_FRACTION:
            logger.warning(
                "sizing_sentinel_triggered symbol=%s basis=%s notional=%.4f account_equity=%.4f "
                "fraction=%.6f confidence_multiplier=%.4f requested_leverage=%.4f",
                symbol,
                sizing_basis,
                notional,
                account_equity,
                notional / account_equity,
                confidence_multiplier,
                requested_leverage,
            )
        return notional

    def _build_risk_state(
        self,
        *,
        paper_run: PaperRun,
        strategy: StrategyContract,
        positions: list[PositionSnapshot],
        symbol: str,
        direction: TradeSide,
        requested_notional: float,
        requested_leverage: float,
        reference_price: Decimal,
        stoploss_price: Decimal,
    ) -> ExecutionRiskState:
        account_equity, equity_source = resolve_paper_account_equity(
            paper_run=paper_run,
            execution_repo=self.execution_repo,
        )
        equity_peak = float(
            paper_run.paper_metrics_summary.get("equity_peak")
            or paper_run.execution_profile.get("equity_peak")
            or account_equity
        )
        if equity_source == "bootstrap_seed":
            logger.warning(
                "account_equity_using_bootstrap_seed paper_run=%s — sizing/exposure may be wrong until "
                "paper_runtime syncs a Testnet snapshot",
                paper_run.paper_run_id,
            )
        total_notional = sum(abs(position.quantity * position.mark_price) for position in positions)
        symbol_notional = sum(
            abs(position.quantity * position.mark_price) for position in positions if position.symbol == symbol
        )
        denominator = account_equity if account_equity > 0 else 1.0
        active_positions = [position for position in positions if abs(position.quantity) > 0]
        correlation_peer_threshold = float(
            paper_run.execution_profile.get("correlation_peer_threshold", DEFAULT_CORRELATION_PEER_THRESHOLD)
        )
        candidate_returns = close_returns(
            [float(bar.close) for bar in self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe="1h", limit=61)]
        )
        correlation_available = not active_positions or candidate_returns is not None
        correlated_cluster_exposure = 0.0
        high_correlation_peer_count = 0
        max_peer_correlation = 0.0
        for position in active_positions:
            if position.symbol == symbol:
                continue
            existing_returns = close_returns(
                [
                    float(bar.close)
                    for bar in self.data_repo.list_ohlcv_bars(
                        symbol=position.symbol,
                        timeframe="1h",
                        limit=61,
                    )
                ]
            )
            coefficient = correlation(candidate_returns or [], existing_returns or [])
            if coefficient is None:
                correlation_available = False
                continue
            if coefficient > correlation_peer_threshold and position.side == direction:
                high_correlation_peer_count += 1
                max_peer_correlation = max(max_peer_correlation, coefficient)
                correlated_cluster_exposure += abs(signed_exposure(position, account_equity=denominator))
        # Discount risk budget by (1-corr) when any same-side peer corr exceeds the
        # threshold, but never collapse the order to near-zero size: the gatekeeper
        # already hard-rejects at >=2 correlated peers (correlated_exposure_limit_exceeded),
        # so this path only ever runs with exactly one correlated peer and should shrink,
        # not zero out, sizing.
        correlation_risk_discount = 1.0
        discounted_notional = float(requested_notional)
        if high_correlation_peer_count > 0 and max_peer_correlation > correlation_peer_threshold:
            correlation_risk_discount = max(0.5, 1.0 - max_peer_correlation)
            discounted_notional = float(requested_notional) * correlation_risk_discount
            logger.warning(
                "correlation_risk_discount_triggered symbol=%s max_peer_correlation=%.4f "
                "peer_threshold=%.4f discount=%.4f requested_notional=%.4f discounted_notional=%.4f",
                symbol,
                max_peer_correlation,
                correlation_peer_threshold,
                correlation_risk_discount,
                requested_notional,
                discounted_notional,
            )
        net_directional_exposure = sum(
            signed_exposure(position, account_equity=denominator) for position in active_positions
        )
        requested_stop_risk_fraction = 0.0
        if account_equity > 0 and reference_price > 0:
            requested_quantity = discounted_notional / float(reference_price)
            requested_stop_risk_fraction = (
                requested_quantity * abs(float(reference_price - stoploss_price)) / account_equity
            )
        assumed_existing_risk = len(active_positions) * float(
            paper_run.execution_profile.get("risk_per_trade", strategy.rules.position_rules.get("risk_per_trade", 0.0))
        )
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
            requested_notional=float(discounted_notional),
            requested_leverage=float(requested_leverage),
            correlated_cluster_exposure=correlated_cluster_exposure,
            high_correlation_peer_count=high_correlation_peer_count,
            correlation_risk_discount=correlation_risk_discount,
            net_directional_exposure=net_directional_exposure,
            portfolio_correlation_available=correlation_available,
            requested_stop_risk_fraction=requested_stop_risk_fraction,
            portfolio_initial_risk_fraction=assumed_existing_risk,
        )

    @staticmethod
    def _round_trip_cost_bps(*, strategy: StrategyContract, symbol: str) -> float:
        rules = strategy.rules.entry_rules
        is_core = symbol.replace(":USDT", "") in {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
        fee = float(rules.get("core_fee_bps" if is_core else "standard_fee_bps", rules.get("fee_bps", 8.0)))
        slippage = float(
            rules.get("core_slippage_bps" if is_core else "standard_slippage_bps", rules.get("slippage_bps", 6.0))
        )
        return 2 * (fee + slippage)


def _is_link_verification_strategy(*, rules, paper_run: PaperRun | None) -> bool:  # noqa: ANN001
    if paper_run is not None and paper_run.execution_profile.get("strategy_lane") == "link_verification":
        return True
    return bool(rules.entry_rules.get("link_verification_only", False))


def _is_cross_sectional_strategy(*, rules, paper_run: PaperRun | None) -> bool:  # noqa: ANN001
    if paper_run is not None and paper_run.execution_profile.get("strategy_lane") == "cross_sectional_carry":
        return True
    return rules.entry_rules.get("strategy_type") == "cross_sectional_funding_carry"


def _is_carry_strategy(*, rules, paper_run: PaperRun | None) -> bool:  # noqa: ANN001
    if paper_run is not None:
        lane = paper_run.execution_profile.get("strategy_lane")
        if lane == "carry":
            return True
        if lane == "directional":
            return False
    return "funding_threshold_bps" in rules.entry_rules
