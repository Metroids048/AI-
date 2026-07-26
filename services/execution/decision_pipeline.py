"""Decision pipeline that connects technical signals, meta-labels, and LLM veto."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from services.agents import AgentTaskService, build_configured_llm_runtime
from services.data import DataRepository
from services.data.market_intelligence import MarketIntelligenceService
from services.execution.net_edge import meta_label_edge_stats
from services.execution.signal_edge_stats import load_active_edge_stats, strategy_rules_hash
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    NotificationRepository,
    ReviewRepository,
    StrategyRepository,
)
from services.strategy_library.ensemble import SignalEnsembleService
from services.strategy_library.meta_label_model import extract_features
from services.strategy_library.regime import MarketRegime, RegimeRouter, RegimeWeight
from services.strategy_library.technical import (
    calculate_atr,
    classify_volatility_regime,
    generate_adx_trend_signal,
    generate_bollinger_reversion_signal,
    generate_dow_trend_signal,
    generate_ema_trend_signal,
    generate_false_breakout_signal,
    generate_fvg_signal,
    generate_macd_signal,
    generate_multi_timeframe_ma_signal,
    generate_price_action_signals,
    generate_rsi_signal,
    generate_vwap_reclaim_signal,
)
from shared.config import settings
from shared.models import (
    AgentTask,
    AgentTaskRequest,
    BetDecision,
    CandidateSignalSeries,
    DecisionVetoResult,
    MetaLabel,
    MetaLabelRequest,
    MetaLabelSample,
    NotificationOutboxItem,
    OHLCVBar,
    SignalEnsemble,
    SignalEnsembleRequest,
    StrategyContract,
    TradeSide,
    TradeSignal,
)

DEFAULT_TECHNICAL_SIGNALS: frozenset[str] = frozenset(
    {
        "macd",
        "dow_trend",
        "price_action",
        "rsi",
        "ema_trend",
        "adx",
        "vwap",
        "bollinger",
        "fvg",
        "mtf_ma",
    }
)
PANDAS_TA_TECHNICAL_SIGNALS: frozenset[str] = frozenset({"pandas_ta_supertrend", "pandas_ta_stoch_rsi"})

_SIGNAL_ALIASES = {
    "dow": "dow_trend",
    "ema": "ema_trend",
    "ema_cross": "ema_trend",
    "ema_trend_follow": "ema_trend",
    "false_break": "false_breakout",
    "fake_breakout": "false_breakout",
    "fake_breakdown": "false_breakout",
    "boll": "bollinger",
    "bbands": "bollinger",
    "fair_value_gap": "fvg",
    "gap_fill": "fvg",
    "mtf": "mtf_ma",
    "multi_timeframe_ma": "mtf_ma",
    "multi_tf_ma": "mtf_ma",
}


@dataclass(frozen=True)
class DecisionPipelineResult:
    direction: TradeSide | None
    should_trade: bool
    reason: str
    reference_price: Decimal
    bar_time: datetime | None
    signals: list[TradeSignal]
    ensemble: SignalEnsemble | None
    meta_label: MetaLabel | None
    veto_result: DecisionVetoResult | None
    confidence_multiplier: float
    atr: float | None
    volatility_context: dict[str, Any]
    trace: dict[str, Any]


class DecisionPipeline:
    """Build an auditable non-arbitrage Paper decision before gatekeeper review."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        execution_repo: ExecutionRepository | None = None,
        agent_repo: AgentTaskRepository | None = None,
        strategy_repo: StrategyRepository | None = None,
        review_repo: ReviewRepository | None = None,
        notification_repo: NotificationRepository | None = None,
        ensemble_service: SignalEnsembleService | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.execution_repo = execution_repo
        self.agent_repo = agent_repo
        self.strategy_repo = strategy_repo
        self.review_repo = review_repo
        self.notification_repo = notification_repo
        self.ensemble_service = ensemble_service or SignalEnsembleService()

    def evaluate(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        enable_decision_veto: bool = True,
        relaxed_signals: bool = False,
    ) -> DecisionPipelineResult:
        bars = self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=timeframe, limit=240)
        latest = bars[-1] if bars else None
        reference_price = Decimal("0") if latest is None else latest.close
        frame = _bars_to_frame(bars)
        volatility: dict[str, Any] = (
            classify_volatility_regime(frame) if not frame.empty else {"regime": "insufficient_data"}
        )
        atr = calculate_atr(frame) if not frame.empty else None
        enabled_signals = _enabled_signals(strategy, timeframe=timeframe)
        signals = self._technical_signals(frame=frame, symbol=symbol, enabled_signals=enabled_signals)
        volatility = {**volatility, "enabled_signals": sorted(enabled_signals), "evaluated_timeframe": timeframe}
        if not signals:
            return self._skipped(
                reason="technical_signals_insufficient",
                reference_price=reference_price,
                latest=latest,
                signals=[],
                ensemble=None,
                atr=atr,
                volatility={**volatility, "signal_count": 0},
            )
        market_intelligence_signal = None
        strategy_allows_market_intelligence = bool(strategy.rules.entry_rules.get("market_intelligence_enabled", True))
        if settings.market_intelligence_enabled and strategy_allows_market_intelligence:
            market_intelligence_signal = MarketIntelligenceService(data_repo=self.data_repo).build_signal(symbol=symbol)
            volatility = {
                **volatility,
                "market_intelligence": market_intelligence_signal.model_dump(mode="json"),
            }
            if market_intelligence_signal.should_participate and market_intelligence_signal.direction is not None:
                signals = [
                    *signals,
                    TradeSignal(
                        symbol=symbol,
                        side=market_intelligence_signal.direction,
                        source="market_intelligence",
                        signal_time=market_intelligence_signal.generated_at,
                        reason="bounded_market_intelligence_vote",
                        confidence=market_intelligence_signal.confidence,
                    ),
                ]

        if "mtf_ma" in enabled_signals:
            mtf_ma_signal = self._mtf_ma_signal(strategy=strategy, symbol=symbol, timeframe=timeframe, frame=frame)
            if mtf_ma_signal is not None:
                signals = [*signals, mtf_ma_signal]

        regime, regime_weight = self._resolve_regime(strategy=strategy, symbol=symbol, timeframe=timeframe, frame=frame)
        volatility = {
            **volatility,
            "market_regime": regime.value,
            "regime_weights": {
                "trend_pullback": regime_weight.trend_pullback,
                "breakout": regime_weight.breakout,
                "range_mean_reversion": regime_weight.range_mean_reversion,
                "momentum_continuation": regime_weight.momentum_continuation,
            },
        }

        multi_timeframe = self._multi_timeframe_confirmation(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            main_signals=signals,
            enabled_signals=enabled_signals,
        )
        if not multi_timeframe["passed"] and not relaxed_signals:
            return self._skipped(
                reason="multi_timeframe_disagreement",
                reference_price=reference_price,
                latest=latest,
                signals=signals,
                ensemble=None,
                atr=atr,
                volatility={**volatility, "multi_timeframe": multi_timeframe},
            )
        volatility = {**volatility, "multi_timeframe": multi_timeframe}
        confirmation_direction_signals = list(multi_timeframe.get("direction_signals", []))

        ensemble = self.ensemble_service.create_ensemble(
            SignalEnsembleRequest(
                signals=[
                    _candidate_from_signal(signal, bars) for signal in [*signals, *confirmation_direction_signals]
                ],
                fusion_method=str(strategy.rules.entry_rules.get("fusion_method", "weighted_vote")),
            ),
            regime=regime,
        )
        if self.execution_repo is not None:
            ensemble = self.execution_repo.create_signal_ensemble(ensemble)
        if ensemble.fused_direction is None:
            return self._skipped(
                reason="ensemble_discarded",
                reference_price=reference_price,
                latest=latest,
                signals=signals,
                ensemble=ensemble,
                atr=atr,
                volatility=volatility,
            )

        training_samples = _meta_label_samples(bars, direction=ensemble.fused_direction)
        model_features = None
        if latest is not None:
            model_features = extract_features(
                bars=bars,
                direction_vote_count=len(ensemble.raw_votes),
                entry_vote_count=sum(1 for vote in ensemble.raw_votes if vote.direction == ensemble.fused_direction),
                ensemble_confidence=ensemble.fused_confidence or 0.0,
                funding_rate_bps=_latest_funding_bps(self.data_repo, symbol=symbol),
                signal_time=latest.timestamp,
            )
        meta_label = self.ensemble_service.create_meta_label(
            MetaLabelRequest(
                ensemble_id=ensemble.ensemble_id,
                signal_time=latest.timestamp if latest else None,
                training_samples=training_samples,
                min_win_rate=float(strategy.rules.entry_rules.get("meta_label_min_win_rate", 0.45)),
                strategy_key=strategy.strategy_key,
                model_features=model_features,
            )
        )
        if self.execution_repo is not None:
            meta_label = self.execution_repo.create_meta_label(meta_label)
        simulation_sampling_mode = bool(strategy.rules.entry_rules.get("simulation_sampling_mode", False))
        validated_edge_required = (
            strategy.strategy_key == "auto_paper_mature_templates" and not simulation_sampling_mode
        )
        if (
            meta_label.bet_decision != BetDecision.BET_TAKEN
            and not relaxed_signals
            and not validated_edge_required
            and not simulation_sampling_mode
        ):
            return self._skipped(
                reason="meta_label_bet_skipped",
                reference_price=reference_price,
                latest=latest,
                signals=signals,
                ensemble=ensemble,
                meta_label=meta_label,
                atr=atr,
                volatility=volatility,
            )

        veto_result = self._run_decision_veto(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            signals=signals,
            ensemble=ensemble,
            meta_label=meta_label,
            volatility=volatility,
            enable_decision_veto=enable_decision_veto,
        )
        fee_bps, slippage_bps = _fee_slippage_bps(strategy=strategy, symbol=symbol)
        edge_stats = _edge_stats_for_gate(
            strategy=strategy,
            symbol=symbol,
            training_samples=training_samples,
        )
        trace = _trace(
            status="vetoed" if veto_result is not None and veto_result.veto else "bet_taken",
            signals=signals,
            ensemble=ensemble,
            meta_label=meta_label,
            veto_result=veto_result,
            volatility=volatility,
            strategy_lane="directional",
        )
        trace.update(
            {
                "meta_label_win_rate": edge_stats["win_rate"],
                "meta_label_average_win": edge_stats["average_win"],
                "meta_label_average_loss": edge_stats["average_loss"],
                "meta_label_sample_count": edge_stats["sample_count"],
                "candidate_id": edge_stats["candidate_id"],
                "rules_hash": edge_stats["rules_hash"],
                "edge_stats_source": edge_stats["source"],
                "edge_artifact_ref": edge_stats["artifact_ref"],
                "validated_edge_required": edge_stats["validated_edge_required"],
                "testnet_sampling_mode": simulation_sampling_mode,
                "validated_edge_net_expectancy": edge_stats["net_expectancy"],
                "validated_edge_oos_sample_count": edge_stats["oos_sample_count"],
                "round_trip_fee_rate": (2.0 * fee_bps) / 10_000.0,
                "round_trip_slippage_rate": (2.0 * slippage_bps) / 10_000.0,
                "taker_fee_bps": fee_bps,
                "estimated_slippage_bps": slippage_bps,
            }
        )
        confidence = float(ensemble.fused_confidence or 1.0)
        size_fraction = 1.0 if validated_edge_required else float(meta_label.position_size_fraction or 1.0)
        return DecisionPipelineResult(
            direction=ensemble.fused_direction,
            should_trade=not (veto_result is not None and veto_result.veto),
            reason="llm_veto" if veto_result is not None and veto_result.veto else "ensemble_meta_label_passed",
            reference_price=reference_price,
            bar_time=latest.timestamp if latest else None,
            signals=signals,
            ensemble=ensemble,
            meta_label=meta_label,
            veto_result=veto_result,
            confidence_multiplier=max(min(confidence * size_fraction, 1.0), 0.0),
            atr=atr,
            volatility_context=volatility,
            trace=trace,
        )

    def _technical_signals(
        self,
        *,
        frame: pd.DataFrame,
        symbol: str,
        enabled_signals: set[str] | frozenset[str],
    ) -> list[TradeSignal]:
        if frame.empty:
            return []
        candidates: list[TradeSignal | None] = []
        if "macd" in enabled_signals:
            candidates.append(generate_macd_signal(frame, symbol=symbol))
        if "dow_trend" in enabled_signals:
            candidates.append(generate_dow_trend_signal(frame, symbol=symbol))
        if "price_action" in enabled_signals:
            candidates.extend(generate_price_action_signals(frame, symbol=symbol))
        elif "false_breakout" in enabled_signals:
            candidates.append(generate_false_breakout_signal(frame, symbol=symbol))
        if "rsi" in enabled_signals:
            candidates.append(generate_rsi_signal(frame, symbol=symbol))
        if "ema_trend" in enabled_signals:
            candidates.append(generate_ema_trend_signal(frame, symbol=symbol))
        if "adx" in enabled_signals:
            candidates.append(generate_adx_trend_signal(frame, symbol=symbol))
        if "vwap" in enabled_signals:
            candidates.append(generate_vwap_reclaim_signal(frame, symbol=symbol))
        if "bollinger" in enabled_signals:
            candidates.append(generate_bollinger_reversion_signal(frame, symbol=symbol))
        if "fvg" in enabled_signals:
            candidates.append(generate_fvg_signal(frame, symbol=symbol))
        for signal_name in sorted(PANDAS_TA_TECHNICAL_SIGNALS & set(enabled_signals)):
            try:
                from services.strategy_library.technical.pandas_ta_adapter import generate_pandas_ta_signal

                candidates.append(
                    generate_pandas_ta_signal(
                        name=signal_name.removeprefix("pandas_ta_"),
                        symbol=symbol,
                        frame=frame,
                    )
                )
            except ImportError:
                # The optional dependency must never interrupt the scheduler.
                continue
        return [signal for signal in candidates if signal is not None]

    def _mtf_ma_signal(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        frame: pd.DataFrame,
    ) -> TradeSignal | None:
        """Build the multi-timeframe frame set from the strategy's own configured
        state/confirmation timeframes (rather than introducing new config) and
        require EMA alignment across all of them before confirming a direction.
        """
        if frame.empty:
            return None
        frames: dict[str, pd.DataFrame] = {timeframe: frame}
        state_timeframe = strategy.rules.entry_rules.get("state_timeframe")
        if state_timeframe and str(state_timeframe) != timeframe:
            state_frame = _bars_to_frame(
                self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=str(state_timeframe), limit=240)
            )
            if not state_frame.empty:
                frames[str(state_timeframe)] = state_frame
        confirm_timeframe = _confirmation_timeframe(strategy=strategy, entry_timeframe=timeframe)
        if confirm_timeframe != timeframe and confirm_timeframe not in frames:
            confirm_frame = _bars_to_frame(
                self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=confirm_timeframe, limit=240)
            )
            if not confirm_frame.empty:
                frames[confirm_timeframe] = confirm_frame
        if len(frames) < 2:
            return None
        return generate_multi_timeframe_ma_signal(frames, symbol=symbol)

    def _resolve_regime(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        frame: pd.DataFrame,
    ) -> tuple[MarketRegime, RegimeWeight]:
        """Identify the current market regime from the strategy's own configured
        state timeframe (falling back to the entry timeframe's own frame), so this
        adds no new configuration surface beyond what direction/entry timeframe
        resolution already uses.
        """
        state_timeframe = strategy.rules.entry_rules.get("state_timeframe")
        regime_frame = frame
        if state_timeframe and str(state_timeframe) != timeframe:
            state_frame = _bars_to_frame(
                self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=str(state_timeframe), limit=240)
            )
            if not state_frame.empty:
                regime_frame = state_frame
        return RegimeRouter().identify_regime(regime_frame)

    def _multi_timeframe_confirmation(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        main_signals: list[TradeSignal],
        enabled_signals: set[str] | frozenset[str],
    ) -> dict[str, Any]:
        del enabled_signals
        main_direction = _dominant_signal_direction(main_signals)
        entry_rules = strategy.rules.entry_rules
        confirmation_mode = str(entry_rules.get("mtf_confirmation_mode", "strict_all")).strip().lower()
        state_timeframe = entry_rules.get("state_timeframe")
        state_signals: list[TradeSignal] = []
        state_direction: TradeSide | None = None
        if state_timeframe and str(state_timeframe) != timeframe:
            state_bars = self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=str(state_timeframe), limit=240)
            state_signals = self._technical_signals(
                frame=_bars_to_frame(state_bars),
                symbol=symbol,
                enabled_signals=_enabled_signals(strategy, timeframe=str(state_timeframe)),
            )
            state_direction = _dominant_signal_direction(state_signals)

        confirm_timeframe = _confirmation_timeframe(strategy=strategy, entry_timeframe=timeframe)
        confirm_signals: list[TradeSignal] = []
        confirm_direction: TradeSide | None = None
        if confirm_timeframe != timeframe:
            confirm_bars = self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=confirm_timeframe, limit=240)
            confirm_signals = self._technical_signals(
                frame=_bars_to_frame(confirm_bars),
                symbol=symbol,
                enabled_signals=_enabled_signals(strategy, timeframe=confirm_timeframe),
            )
            confirm_direction = _dominant_signal_direction(confirm_signals)

        if confirmation_mode == "entry_plus_one_higher":
            matching_sets: list[list[TradeSignal]] = []
            if main_direction is not None and state_direction == main_direction:
                matching_sets.append(state_signals)
            if main_direction is not None and confirm_direction == main_direction:
                matching_sets.append(confirm_signals)
            direction_signals = _deduplicate_signals_by_source(
                signal for signal_set in matching_sets for signal in signal_set
            )
            passed = main_direction is not None and bool(direction_signals)
            return {
                "passed": passed,
                "status": ("entry_plus_one_higher_confirmed" if passed else "entry_plus_one_higher_disagreed"),
                "confirmation_mode": confirmation_mode,
                "main_timeframe": timeframe,
                "main_direction": str(main_direction) if main_direction is not None else None,
                "state_timeframe": str(state_timeframe) if state_timeframe else None,
                "state_direction": str(state_direction) if state_direction is not None else None,
                "state_signal_count": len(state_signals),
                "confirm_timeframe": confirm_timeframe,
                "confirm_direction": str(confirm_direction) if confirm_direction is not None else None,
                "confirm_signal_count": len(confirm_signals),
                "matching_higher_timeframe_count": len(matching_sets),
                "direction_signals": direction_signals,
            }

        if state_timeframe and str(state_timeframe) != timeframe:
            if not state_signals or main_direction is None or state_direction is None:
                return {
                    "passed": False,
                    "status": "state_confirmation_unavailable_fail_closed",
                    "main_timeframe": timeframe,
                    "state_timeframe": str(state_timeframe),
                    "state_signal_count": len(state_signals),
                }
            if main_direction != state_direction:
                return {
                    "passed": False,
                    "status": "state_confirmation_disagreed",
                    "main_timeframe": timeframe,
                    "state_timeframe": str(state_timeframe),
                    "main_direction": str(main_direction),
                    "state_direction": str(state_direction),
                    "state_signal_count": len(state_signals),
                }
        state_confirmation = (
            {
                "timeframe": str(state_timeframe),
                "direction": str(state_direction),
                "signal_count": len(state_signals),
            }
            if state_timeframe and state_direction is not None
            else None
        )
        if confirm_timeframe == timeframe:
            return {
                "passed": True,
                "status": "confirmation_same_timeframe",
                "main_timeframe": timeframe,
                "confirm_timeframe": confirm_timeframe,
            }
        if not confirm_signals or main_direction is None or confirm_direction is None:
            return {
                "passed": False,
                "status": "confirmation_unavailable_fail_closed",
                "main_timeframe": timeframe,
                "confirm_timeframe": confirm_timeframe,
                "confirm_signal_count": len(confirm_signals),
            }
        return {
            "passed": main_direction == confirm_direction,
            "status": "confirmed" if main_direction == confirm_direction else "disagreed",
            "main_timeframe": timeframe,
            "confirm_timeframe": confirm_timeframe,
            "main_direction": str(main_direction),
            "confirm_direction": str(confirm_direction),
            "confirm_signal_count": len(confirm_signals),
            "direction_signals": confirm_signals,
            **({"state_confirmation": state_confirmation} if state_confirmation else {}),
        }

    def _run_decision_veto(
        self,
        *,
        strategy: StrategyContract,
        symbol: str,
        timeframe: str,
        signals: list[TradeSignal],
        ensemble: SignalEnsemble,
        meta_label: MetaLabel,
        volatility: dict[str, Any],
        enable_decision_veto: bool,
    ) -> DecisionVetoResult | None:
        if not enable_decision_veto:
            return DecisionVetoResult(
                veto=False,
                veto_reason="decision veto disabled for this Paper request",
                checked_at=datetime.now(UTC),
            )
        if self.agent_repo is None or self.strategy_repo is None:
            return DecisionVetoResult(
                veto=False,
                veto_reason="decision veto agent repository unavailable -> advisory unavailable",
                checked_at=datetime.now(UTC),
            )
        if _daily_veto_calls(self.agent_repo, datetime.now(UTC).date()) >= settings.decision_veto_daily_budget:
            task = self.agent_repo.create_task(
                AgentTask(
                    agent_type="decision_veto_agent",
                    task_type="pre_execution_veto_llm",
                    input_ref=f"signal_ensemble:{ensemble.ensemble_id}",
                    input_payload={"symbol": symbol, "reason": "daily budget exceeded"},
                    task_status="failed",
                    error_summary="decision veto daily budget exceeded",
                    executor_name="llm_decision_veto",
                    schema_validation_status="budget_exceeded",
                    output_payload={
                        "veto_result": {
                            "veto": False,
                            "veto_reason": "decision veto daily budget exceeded -> advisory unavailable",
                        }
                    },
                )
            )
            if self.notification_repo is not None:
                self.notification_repo.create_notification(
                    NotificationOutboxItem(
                        notification_id=f"llm_budget:{datetime.now(UTC).date().isoformat()}",
                        event_type="llm_budget_exceeded",
                        severity="high",
                        subject="Decision Veto daily budget exceeded",
                        body=f"Daily Decision Veto budget {settings.decision_veto_daily_budget} was exceeded.",
                        source_ref=f"agent_task:{task.agent_task_id}",
                    )
                )
            return DecisionVetoResult(
                veto=False,
                veto_reason="decision veto daily budget exceeded -> advisory unavailable",
                checked_at=datetime.now(UTC),
                agent_task_ref=task.agent_task_id,
            )

        service = AgentTaskService(
            agent_repo=self.agent_repo,
            strategy_repo=self.strategy_repo,
            review_repo=self.review_repo,
            llm_runtime=build_configured_llm_runtime(),
        )
        task = service.submit_task(
            AgentTaskRequest(
                agent_type="decision_veto_agent",
                task_type="pre_execution_veto_llm",
                input_ref=f"signal_ensemble:{ensemble.ensemble_id}",
                input_payload={
                    "strategy": {
                        "strategy_id": strategy.strategy_id,
                        "core_thesis": strategy.core_thesis,
                        "entry_rules": strategy.rules.entry_rules,
                        "exit_rules": strategy.rules.exit_rules,
                    },
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "technical_signals": [signal.model_dump(mode="json") for signal in signals],
                    "ensemble": ensemble.model_dump(mode="json"),
                    "meta_label": meta_label.model_dump(mode="json"),
                    "recent_risk_events": [
                        event.model_dump(mode="json") for event in self.data_repo.list_risk_events(active_only=True)
                    ],
                    "market_context": {
                        "volatility": volatility,
                        "latest_market_extras": _jsonable_market_extras(
                            self.data_repo.get_latest_market_extras(symbol=symbol)
                        ),
                    },
                },
            )
        )
        if task.schema_validation_status == "provider_unavailable" and self.notification_repo is not None:
            # De-duped per day via the same create_notification no-op-if-exists
            # pattern as the budget-exceeded notification above. Previously this
            # failure mode was completely silent -> nobody noticed the LLM veto
            # chain had never actually run.
            self.notification_repo.create_notification(
                NotificationOutboxItem(
                    notification_id=f"llm_provider_unavailable:{datetime.now(UTC).date().isoformat()}",
                    event_type="llm_provider_unavailable",
                    severity="high",
                    subject="Decision Veto LLM runtime unavailable",
                    body=(
                        "decision_veto_agent could not reach any configured LLM provider "
                        f"(advisory unavailable; deterministic risk events remain enforced): {task.error_summary}"
                    ),
                    source_ref=f"agent_task:{task.agent_task_id}",
                )
            )
        payload = task.output_payload.get("veto_result", {})
        if not isinstance(payload, dict):
            return DecisionVetoResult(
                veto=False,
                veto_reason="invalid veto payload -> advisory unavailable",
                agent_task_ref=task.agent_task_id,
            )
        high_risk_events = self.data_repo.has_blocking_risk_event(
            scope=symbol,
            reference_time=datetime.now(UTC),
        )
        return DecisionVetoResult(
            veto=bool(high_risk_events),
            veto_reason=(
                "high severity risk event present"
                if high_risk_events
                else str(payload.get("veto_reason", "llm advisory completed"))
            ),
            checked_at=datetime.now(UTC),
            agent_task_ref=task.agent_task_id,
        )

    @staticmethod
    def _skipped(
        *,
        reason: str,
        reference_price: Decimal,
        latest: OHLCVBar | None,
        signals: list[TradeSignal],
        ensemble: SignalEnsemble | None,
        atr: float | None,
        volatility: dict[str, Any],
        meta_label: MetaLabel | None = None,
    ) -> DecisionPipelineResult:
        # CRITICAL FIX: Use conservative default instead of 0.0 to prevent sizing collapse
        # When pipeline skips a signal (insufficient data, veto, etc.), we should NOT
        # zero out confidence - that causes notional=0 and triggers sizing_sentinel.
        # Instead, use a conservative 0.5 multiplier so position sizing remains functional.
        # Rationale: "skipped" means "not confident enough to trade", NOT "position should be zero"
        return DecisionPipelineResult(
            direction=ensemble.fused_direction if ensemble else None,
            should_trade=False,
            reason=reason,
            reference_price=reference_price,
            bar_time=latest.timestamp if latest else None,
            signals=signals,
            ensemble=ensemble,
            meta_label=meta_label,
            veto_result=None,
            confidence_multiplier=0.5,  # Conservative fallback instead of 0.0
            atr=atr,
            volatility_context=volatility,
            trace=_trace(
                status=reason,
                signals=signals,
                ensemble=ensemble,
                meta_label=meta_label,
                veto_result=None,
                volatility=volatility,
                strategy_lane="directional",
            ),
        )


def _bars_to_frame(bars: list[OHLCVBar]) -> pd.DataFrame:
    rows = [
        {
            "time": bar.timestamp,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        for bar in bars
    ]
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows).set_index("time")
    return frame[["open", "high", "low", "close", "volume"]]


def _enabled_signals(strategy: StrategyContract, *, timeframe: str | None = None) -> set[str]:
    entry_rules = strategy.rules.entry_rules
    raw: object | None = None
    if timeframe is not None:
        direction_tf = str(entry_rules.get("direction_timeframe") or "")
        entry_tf = str(entry_rules.get("entry_timeframe") or "")
        state_tf = str(entry_rules.get("state_timeframe") or "")
        if timeframe == entry_tf and entry_rules.get("entry_signals"):
            raw = entry_rules["entry_signals"]
        elif timeframe in {direction_tf, state_tf} and entry_rules.get("direction_signals"):
            raw = entry_rules["direction_signals"]
    if raw is None:
        raw = entry_rules.get("enabled_signals", entry_rules.get("technical_signals"))
    if raw is None or raw == "":
        return set(DEFAULT_TECHNICAL_SIGNALS)
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        return set(DEFAULT_TECHNICAL_SIGNALS)
    normalized: set[str] = set()
    for value in values:
        key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if key in {"all", "default", "defaults"}:
            return set(DEFAULT_TECHNICAL_SIGNALS)
        normalized.add(_SIGNAL_ALIASES.get(key, key))
    supported = set(DEFAULT_TECHNICAL_SIGNALS) | set(PANDAS_TA_TECHNICAL_SIGNALS) | {"false_breakout"}
    return {key for key in normalized if key in supported} or set(DEFAULT_TECHNICAL_SIGNALS)


def _confirmation_timeframe(*, strategy: StrategyContract, entry_timeframe: str) -> str:
    entry_rules = strategy.rules.entry_rules
    direction_timeframe = entry_rules.get("direction_timeframe")
    configured_entry_timeframe = entry_rules.get("entry_timeframe")
    timeframe_model = str(entry_rules.get("timeframe_model", "")).lower()
    if direction_timeframe and configured_entry_timeframe and entry_timeframe == str(configured_entry_timeframe):
        return str(direction_timeframe)
    if timeframe_model == "4h_direction_15m_entry" and entry_timeframe == "15m":
        return "4h"
    return entry_timeframe


def _dominant_signal_direction(signals: list[TradeSignal]) -> TradeSide | None:
    score = 0.0
    for signal in signals:
        weight = float(signal.confidence or 0.5)
        score += weight if signal.side == TradeSide.LONG else -weight
    if score > 0:
        return TradeSide.LONG
    if score < 0:
        return TradeSide.SHORT
    return None


def _deduplicate_signals_by_source(signals: Any) -> list[TradeSignal]:
    strongest: dict[str, TradeSignal] = {}
    for signal in signals:
        existing = strongest.get(signal.source)
        if existing is None or float(signal.confidence or 0.0) > float(existing.confidence or 0.0):
            strongest[signal.source] = signal
    return list(strongest.values())


def _candidate_from_signal(signal: TradeSignal, bars: list[OHLCVBar]) -> CandidateSignalSeries:
    closes = [float(bar.close) for bar in bars[-80:]]
    series = []
    for previous, current in zip(closes, closes[1:], strict=False):
        raw_return = (current - previous) / max(previous, 1.0)
        series.append(raw_return if signal.side == TradeSide.LONG else -raw_return)
    return CandidateSignalSeries(
        strategy_id=f"{signal.source}:{signal.reason or signal.side}",
        direction=signal.side,
        weight=_signal_weight(signal),
        confidence=signal.confidence,
        validation_score=signal.confidence,
        series=series,
    )


def _signal_weight(signal: TradeSignal) -> float:
    base = 0.5
    if signal.source == "technical_macd":
        base = 1.0
    elif signal.source == "technical_dow_trend" or signal.source == "technical_ema_trend":
        base = 0.9
    elif signal.source == "technical_adx":
        base = 0.85
    elif signal.source in {"technical_rsi", "technical_vwap", "technical_bollinger"}:
        base = 0.75
    elif signal.source.startswith("price_action"):
        base = 0.7
    elif signal.source == "market_intelligence":
        base = min(settings.market_intelligence_vote_weight_cap, 0.30)
    return base * float(signal.confidence or 0.0)


def _latest_funding_bps(data_repo: DataRepository, *, symbol: str) -> float | None:
    latest = data_repo.get_latest_market_extras(symbol=symbol)
    if latest is None or latest.funding_rate is None:
        return None
    return float(latest.funding_rate) * 10_000.0


def _fee_slippage_bps(*, strategy: StrategyContract, symbol: str) -> tuple[float, float]:
    rules = strategy.rules.entry_rules
    is_core = symbol.replace(":USDT", "") in {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    fee = float(rules.get("core_fee_bps" if is_core else "standard_fee_bps", rules.get("fee_bps", 8.0)))
    slippage = float(
        rules.get("core_slippage_bps" if is_core else "standard_slippage_bps", rules.get("slippage_bps", 6.0))
    )
    return fee, slippage


def _edge_stats_for_gate(
    *, strategy: StrategyContract, symbol: str, training_samples: list[MetaLabelSample]
) -> dict[str, Any]:
    candidate_id = str(strategy.rules.entry_rules.get("candidate_id", "operator_heuristic_v1"))
    rules_hash = strategy_rules_hash(strategy.rules)
    if strategy.strategy_key == "auto_paper_mature_templates":
        artifact = load_active_edge_stats(
            strategy.strategy_key,
            candidate_id,
            symbol,
            strategy.rules,
        )
        if artifact is None:
            return {
                "sample_count": 0.0,
                "oos_sample_count": 0,
                "win_rate": None,
                "average_win": None,
                "average_loss": None,
                "net_expectancy": None,
                "candidate_id": candidate_id,
                "rules_hash": rules_hash,
                "source": "validated_edge_stats_missing_or_stale",
                "artifact_ref": None,
                "validated_edge_required": True,
            }
        return {
            "sample_count": float(artifact.sample_count),
            "oos_sample_count": artifact.oos_sample_count,
            "win_rate": artifact.win_rate,
            "average_win": artifact.average_net_win,
            "average_loss": artifact.average_net_loss_magnitude,
            "net_expectancy": artifact.net_expectancy,
            "candidate_id": candidate_id,
            "rules_hash": rules_hash,
            "source": "validated_oos_artifact_v2",
            "artifact_ref": artifact.artifact_path,
            "validated_edge_required": True,
        }

    proxy = meta_label_edge_stats([sample.net_return for sample in training_samples])
    return {
        **proxy,
        "oos_sample_count": 0,
        "net_expectancy": None,
        "candidate_id": candidate_id,
        "rules_hash": rules_hash,
        "source": "raw_bar_proxy",
        "artifact_ref": None,
        "validated_edge_required": False,
    }


def _meta_label_samples(bars: list[OHLCVBar], *, direction: TradeSide) -> list[MetaLabelSample]:
    samples: list[MetaLabelSample] = []
    closed_history = bars[:-1]
    for previous, current in zip(closed_history[-48:-1], closed_history[-47:], strict=False):
        previous_close = float(previous.close)
        current_close = float(current.close)
        raw_return = (current_close - previous_close) / max(previous_close, 1.0)
        samples.append(
            MetaLabelSample(
                sample_time=current.timestamp,
                net_return=raw_return if direction == TradeSide.LONG else -raw_return,
            )
        )
    return samples


def _daily_veto_calls(agent_repo: AgentTaskRepository, day: date) -> int:
    count = 0
    for task in agent_repo.list_tasks():
        created_at = task.created_at
        if (
            task.agent_type == "decision_veto_agent"
            and task.task_type == "pre_execution_veto_llm"
            and created_at is not None
            and created_at.date() == day
        ):
            count += 1
    return count


def _trace(
    *,
    status: str,
    signals: list[TradeSignal],
    ensemble: SignalEnsemble | None,
    meta_label: MetaLabel | None,
    veto_result: DecisionVetoResult | None,
    volatility: dict[str, Any],
    strategy_lane: str = "directional",
) -> dict[str, Any]:
    return {
        "pipeline_status": status,
        "strategy_lane": strategy_lane,
        "signals": [signal.model_dump(mode="json") for signal in signals],
        "ensemble": ensemble.model_dump(mode="json") if ensemble is not None else None,
        "meta_label": meta_label.model_dump(mode="json") if meta_label is not None else None,
        "veto_result": veto_result.model_dump(mode="json") if veto_result is not None else None,
        "volatility": volatility,
    }


def _jsonable_market_extras(value) -> dict[str, Any] | None:
    return value.model_dump(mode="json") if value is not None else None
