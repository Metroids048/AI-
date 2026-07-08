"""Decision pipeline that connects technical signals, meta-labels, and LLM veto."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from shared.config import settings
from services.agents import (
    AgentTaskService,
    ConfiguredStructuredLLMRuntime,
    FallbackChainStructuredLLMRuntime,
    OpenAICompatibleStructuredLLMRuntime,
    UnavailableLLMRuntime,
)
from services.agents.llm_runtime import (
    discover_github_models_free_models,
    discover_openrouter_free_models,
    parse_model_override,
)
from services.data import DataRepository
from services.strategy_library import (
    AgentTaskRepository,
    ExecutionRepository,
    NotificationRepository,
    ReviewRepository,
    StrategyRepository,
)
from services.strategy_library.ensemble import SignalEnsembleService
from services.strategy_library.technical import (
    calculate_atr,
    classify_volatility_regime,
    generate_dow_trend_signal,
    generate_macd_signal,
    generate_price_action_signals,
)
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
        signals = self._technical_signals(frame=frame, symbol=symbol)
        fallback_direction = _fallback_direction(bars)
        if not signals:
            trace: dict[str, Any] = {
                "pipeline_status": "fallback_direction",
                "fallback_reason": "technical_signals_insufficient",
                "signals": [],
                "volatility": volatility,
            }
            return DecisionPipelineResult(
                direction=fallback_direction,
                should_trade=fallback_direction is not None,
                reason="fallback_direction" if fallback_direction is not None else "insufficient_market_data",
                reference_price=reference_price,
                bar_time=latest.timestamp if latest else None,
                signals=[],
                ensemble=None,
                meta_label=None,
                veto_result=None,
                confidence_multiplier=1.0,
                atr=atr,
                volatility_context=volatility,
                trace=trace,
            )

        multi_timeframe = self._multi_timeframe_confirmation(
            symbol=symbol,
            timeframe=timeframe,
            main_signals=signals,
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

        ensemble = self.ensemble_service.create_ensemble(
            SignalEnsembleRequest(signals=[_candidate_from_signal(signal, bars) for signal in signals])
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

        meta_label = self.ensemble_service.create_meta_label(
            MetaLabelRequest(
                ensemble_id=ensemble.ensemble_id,
                signal_time=latest.timestamp if latest else None,
                training_samples=_meta_label_samples(bars, direction=ensemble.fused_direction),
                min_win_rate=float(strategy.rules.entry_rules.get("meta_label_min_win_rate", 0.45)),
            )
        )
        if self.execution_repo is not None:
            meta_label = self.execution_repo.create_meta_label(meta_label)
        if meta_label.bet_decision != BetDecision.BET_TAKEN and not relaxed_signals:
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
        trace = _trace(
            status="vetoed" if veto_result is not None and veto_result.veto else "bet_taken",
            signals=signals,
            ensemble=ensemble,
            meta_label=meta_label,
            veto_result=veto_result,
            volatility=volatility,
        )
        confidence = float(ensemble.fused_confidence or 1.0)
        size_fraction = float(meta_label.position_size_fraction or 1.0)
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

    def _technical_signals(self, *, frame: pd.DataFrame, symbol: str) -> list[TradeSignal]:
        if frame.empty:
            return []
        candidates = [
            generate_macd_signal(frame, symbol=symbol),
            generate_dow_trend_signal(frame, symbol=symbol),
            *generate_price_action_signals(frame, symbol=symbol),
        ]
        return [signal for signal in candidates if signal is not None]

    def _multi_timeframe_confirmation(
        self,
        *,
        symbol: str,
        timeframe: str,
        main_signals: list[TradeSignal],
    ) -> dict[str, Any]:
        confirm_timeframe = "15m" if timeframe != "15m" else "1h"
        confirm_bars = self.data_repo.list_ohlcv_bars(symbol=symbol, timeframe=confirm_timeframe, limit=240)
        confirm_frame = _bars_to_frame(confirm_bars)
        confirm_signals = self._technical_signals(frame=confirm_frame, symbol=symbol)
        main_direction = _dominant_signal_direction(main_signals)
        confirm_direction = _dominant_signal_direction(confirm_signals)
        if not confirm_bars or not confirm_signals or main_direction is None or confirm_direction is None:
            return {
                "passed": True,
                "status": "confirmation_unavailable",
                "main_timeframe": timeframe,
                "confirm_timeframe": confirm_timeframe,
            }
        return {
            "passed": main_direction == confirm_direction,
            "status": "confirmed" if main_direction == confirm_direction else "disagreed",
            "main_timeframe": timeframe,
            "confirm_timeframe": confirm_timeframe,
            "main_direction": str(main_direction),
            "confirm_direction": str(confirm_direction),
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
                veto=True,
                veto_reason="decision veto agent repository unavailable -> fail closed",
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
                            "veto": True,
                            "veto_reason": "decision veto daily budget exceeded -> fail closed",
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
                veto=True,
                veto_reason="decision veto daily budget exceeded -> fail closed",
                checked_at=datetime.now(UTC),
                agent_task_ref=task.agent_task_id,
            )

        service = AgentTaskService(
            agent_repo=self.agent_repo,
            strategy_repo=self.strategy_repo,
            review_repo=self.review_repo,
            llm_runtime=_configured_llm_runtime(),
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
        payload = task.output_payload.get("veto_result", {})
        if not isinstance(payload, dict):
            return DecisionVetoResult(
                veto=True,
                veto_reason="invalid veto payload -> fail closed",
                agent_task_ref=task.agent_task_id,
            )
        return DecisionVetoResult(
            veto=bool(payload.get("veto", True)),
            veto_reason=str(payload.get("veto_reason", "missing veto reason -> fail closed")),
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
            confidence_multiplier=0.0,
            atr=atr,
            volatility_context=volatility,
            trace=_trace(
                status=reason,
                signals=signals,
                ensemble=ensemble,
                meta_label=meta_label,
                veto_result=None,
                volatility=volatility,
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


def _fallback_direction(bars: list[OHLCVBar]) -> TradeSide | None:
    if len(bars) < 2:
        return None
    return TradeSide.SHORT if bars[-1].close < bars[-2].close else TradeSide.LONG


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
    elif signal.source == "technical_dow_trend":
        base = 0.9
    elif signal.source.startswith("price_action"):
        base = 0.7
    return base * float(signal.confidence or 0.0)


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


def _configured_llm_runtime():
    runtimes = []
    if settings.claude_api_key:
        runtimes.append(
            ConfiguredStructuredLLMRuntime(
                anthropic_api_key=settings.claude_api_key,
                default_model=settings.claude_model,
                anthropic_base_url=settings.anthropic_api_base_url,
                provider_by_agent=json.loads(settings.agent_llm_provider_map or "{}"),
                model_by_agent=json.loads(settings.agent_llm_model_map or "{}"),
            )
        )
    if settings.openrouter_api_key:
        models = parse_model_override(settings.openrouter_free_models) or discover_openrouter_free_models(
            api_key=settings.openrouter_api_key,
            cache_seconds=settings.llm_free_model_catalog_cache_seconds,
        )
        runtimes.extend(
            OpenAICompatibleStructuredLLMRuntime(
                api_key=settings.openrouter_api_key,
                model=model,
                base_url="https://openrouter.ai/api/v1",
                provider_label="openrouter",
            )
            for model in models
        )
    if settings.github_models_token:
        models = parse_model_override(settings.github_models_free_models) or discover_github_models_free_models(
            token=settings.github_models_token,
            cache_seconds=settings.llm_free_model_catalog_cache_seconds,
        )
        runtimes.extend(
            OpenAICompatibleStructuredLLMRuntime(
                api_key=settings.github_models_token,
                model=model,
                base_url="https://models.inference.ai.azure.com",
                provider_label="github_models",
            )
            for model in models
        )
    if not runtimes:
        return UnavailableLLMRuntime()
    if len(runtimes) == 1:
        return runtimes[0]
    return FallbackChainStructuredLLMRuntime(runtimes)


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
) -> dict[str, Any]:
    return {
        "pipeline_status": status,
        "signals": [signal.model_dump(mode="json") for signal in signals],
        "ensemble": ensemble.model_dump(mode="json") if ensemble is not None else None,
        "meta_label": meta_label.model_dump(mode="json") if meta_label is not None else None,
        "veto_result": veto_result.model_dump(mode="json") if veto_result is not None else None,
        "volatility": volatility,
    }


def _jsonable_market_extras(value) -> dict[str, Any] | None:
    return value.model_dump(mode="json") if value is not None else None
