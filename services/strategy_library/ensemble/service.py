"""SignalEnsemble and MetaLabel business logic."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from statistics import mean

from services.strategy_library.meta_label_model import load_active_model, predict_win_probability
from shared.models import (
    BetDecision,
    CandidateSignalSeries,
    EnsembleStatus,
    MetaLabel,
    MetaLabelRequest,
    SignalEnsemble,
    SignalEnsembleRequest,
    SignalVote,
    TradeSide,
    TripleBarrierOutcome,
)

DIRECTION_SOURCES = frozenset(
    {"technical_dow_trend", "technical_ema_trend", "technical_adx", "technical_mtf_ma"}
)
ENTRY_SOURCES = frozenset({"technical_macd", "technical_rsi", "technical_fvg", "market_intelligence"})
RANGE_SOURCES = frozenset({"technical_vwap", "technical_bollinger"})
LAYERED_FUSION_METHOD = "layered_regime_entry"
LAYERED_RELAXED_FUSION_METHOD = "layered_regime_entry_relaxed"

# Minimum number of DIRECTION_SOURCES that must report before a direction can be
# resolved at all (fail-closed on thin data), and the quorum a majority is judged
# against. Originally all 3 direction sources had to unanimously agree; that strict
# AND-gate was starving entries. This keeps failing closed on missing data or exact
# ties, but now allows a clear majority (e.g. 2-of-3, or 3-of-4 once mtf_ma reports)
# to resolve a direction instead of requiring unanimity.
MIN_DIRECTION_SOURCE_QUORUM = 3
RELAXED_DIRECTION_SOURCE_QUORUM = 2


class SignalEnsembleService:
    """Fuse low-correlation signal candidates into a single trade candidate."""

    def create_ensemble(self, request: SignalEnsembleRequest) -> SignalEnsemble:
        if not request.signals:
            raise ValueError("at least one signal is required")
        if request.fusion_method in {LAYERED_FUSION_METHOD, LAYERED_RELAXED_FUSION_METHOD}:
            min_direction_sources = (
                RELAXED_DIRECTION_SOURCE_QUORUM
                if request.fusion_method == LAYERED_RELAXED_FUSION_METHOD
                else MIN_DIRECTION_SOURCE_QUORUM
            )
            return self._create_layered_ensemble(request, min_direction_sources=min_direction_sources)
        return self._create_weighted_ensemble(request)

    def _create_weighted_ensemble(self, request: SignalEnsembleRequest) -> SignalEnsemble:
        adjusted = self._correlation_filter(
            request.signals,
            threshold=request.correlation_threshold,
            min_history=request.min_history,
        )
        votes = [
            SignalVote(
                strategy_id=signal.strategy_id,
                direction=signal.direction,
                weight=weight,
                confidence=signal.confidence,
            )
            for signal, weight in adjusted
            if weight > 0
        ]
        return self._finalize_votes(
            request=request,
            votes=votes,
            audit={
                "correlation_threshold": request.correlation_threshold,
                "min_history": request.min_history,
                "input_count": len(request.signals),
                "kept_count": len(votes),
            },
            correlation_prefix="correlation_filter",
        )

    def _create_layered_ensemble(
        self, request: SignalEnsembleRequest, *, min_direction_sources: int
    ) -> SignalEnsemble:
        allowed_direction = self._resolve_allowed_direction(
            request.signals, min_direction_sources=min_direction_sources
        )
        eligible = self._eligible_layered_signals(request.signals, allowed_direction=allowed_direction)
        adjusted = self._correlation_filter(
            eligible,
            threshold=request.correlation_threshold,
            min_history=request.min_history,
        )
        votes = [
            SignalVote(
                strategy_id=signal.strategy_id,
                direction=signal.direction,
                weight=weight,
                confidence=signal.confidence,
            )
            for signal, weight in adjusted
            if weight > 0
        ]
        direction_label = "none" if allowed_direction is None else str(allowed_direction.value)
        return self._finalize_votes(
            request=request,
            votes=votes,
            audit={
                "fusion_method": request.fusion_method,
                "min_direction_sources": min_direction_sources,
                "allowed_direction": direction_label,
                "correlation_threshold": request.correlation_threshold,
                "min_history": request.min_history,
                "input_count": len(request.signals),
                "eligible_count": len(eligible),
                "kept_count": len(votes),
            },
            correlation_prefix=f"{request.fusion_method}:allowed_direction={direction_label}",
        )

    def _finalize_votes(
        self,
        *,
        request: SignalEnsembleRequest,
        votes: list[SignalVote],
        audit: dict[str, object],
        correlation_prefix: str,
    ) -> SignalEnsemble:
        if not votes:
            return SignalEnsemble(
                ensemble_id=str(uuid.uuid4()),
                strategy_refs=[signal.strategy_id for signal in request.signals],
                fusion_method=request.fusion_method,
                raw_votes=[],
                ensemble_status=EnsembleStatus.DISCARDED_LOW_CONFIDENCE,
                correlation_matrix_ref=f"{correlation_prefix}:{audit}",
                created_at=datetime.now(UTC),
            )
        long_score = sum(v.weight * (v.confidence or 1.0) for v in votes if v.direction == TradeSide.LONG)
        short_score = sum(v.weight * (v.confidence or 1.0) for v in votes if v.direction == TradeSide.SHORT)
        total_score = long_score + short_score
        if long_score >= short_score:
            direction = TradeSide.LONG
            confidence = long_score / total_score if total_score else 0.0
        else:
            direction = TradeSide.SHORT
            confidence = short_score / total_score if total_score else 0.0
        return SignalEnsemble(
            ensemble_id=str(uuid.uuid4()),
            strategy_refs=[vote.strategy_id for vote in votes],
            fusion_method=request.fusion_method,
            correlation_matrix_ref=f"{correlation_prefix}:{audit}",
            raw_votes=votes,
            fused_direction=direction,
            fused_confidence=confidence,
            ensemble_status=EnsembleStatus.PASSED_TO_META_LABEL,
            created_at=datetime.now(UTC),
        )

    def create_meta_label(self, request: MetaLabelRequest) -> MetaLabel:
        if request.signal_time is not None:
            future_samples = [
                sample for sample in request.training_samples if sample.sample_time >= request.signal_time
            ]
            if future_samples:
                raise ValueError("training samples must be earlier than signal_time")
        returns = [sample.net_return for sample in request.training_samples]
        rule_wins = [value for value in returns if value > 0]
        rule_win_rate = len(rule_wins) / len(returns) if returns else 0.0
        average_return = mean(returns) if returns else 0.0

        win_rate = rule_win_rate
        model_ref = "rule_meta_label_v1"
        if request.strategy_key is not None and request.model_features is not None:
            model = load_active_model(request.strategy_key)
            if model is not None:
                predicted = predict_win_probability(model, request.model_features)
                if predicted is not None:
                    win_rate = predicted
                    model_ref = f"trained_meta_label:{model.strategy_key}:{model.version}"

        bet_taken = (
            len(returns) >= request.min_training_samples
            and win_rate >= request.min_win_rate
            and average_return > request.min_average_return
        )
        if not returns:
            outcome = TripleBarrierOutcome.TIMEOUT
        elif average_return >= request.take_profit:
            outcome = TripleBarrierOutcome.TAKE_PROFIT
        elif average_return <= request.stop_loss:
            outcome = TripleBarrierOutcome.STOP_LOSS
        else:
            outcome = TripleBarrierOutcome.TIMEOUT
        position_size = None
        if bet_taken:
            edge_score = min(max((win_rate - 0.5) * 2.0 + max(average_return, 0.0) * 10.0, 0.1), 1.0)
            position_size = edge_score
        return MetaLabel(
            meta_label_id=str(uuid.uuid4()),
            ensemble_id=request.ensemble_id,
            triple_barrier_result=outcome,
            bet_decision=BetDecision.BET_TAKEN if bet_taken else BetDecision.BET_SKIPPED,
            position_size_fraction=position_size,
            model_ref=model_ref,
            training_window_ref=self._training_ref(request),
        )

    def _correlation_filter(
        self,
        signals: list[CandidateSignalSeries],
        *,
        threshold: float,
        min_history: int,
    ) -> list[tuple[CandidateSignalSeries, float]]:
        weights = {signal.strategy_id: signal.weight for signal in signals}
        for index, left in enumerate(signals):
            for right in signals[index + 1 :]:
                if len(left.series) < min_history or len(right.series) < min_history:
                    continue
                corr = _pearson(left.series, right.series)
                if abs(corr) < threshold:
                    continue
                left_score = left.validation_score if left.validation_score is not None else left.weight
                right_score = right.validation_score if right.validation_score is not None else right.weight
                weaker = right if left_score >= right_score else left
                weights[weaker.strategy_id] *= 0.25
        return [(signal, weights[signal.strategy_id]) for signal in signals]

    def _training_ref(self, request: MetaLabelRequest) -> str:
        if not request.training_samples:
            return "empty_training_window"
        ordered = sorted(sample.sample_time for sample in request.training_samples)
        return f"{ordered[0].isoformat()}..{ordered[-1].isoformat()}:{len(ordered)}"

    @staticmethod
    def _signal_source(strategy_id: str) -> str:
        return strategy_id.split(":", 1)[0]

    @classmethod
    def _signal_layer(cls, strategy_id: str) -> str:
        source = cls._signal_source(strategy_id)
        if source in DIRECTION_SOURCES:
            return "direction"
        if source in RANGE_SOURCES:
            return "range"
        if source in ENTRY_SOURCES or source.startswith("price_action"):
            return "entry"
        return "entry"

    @classmethod
    def _resolve_allowed_direction(
        cls,
        signals: list[CandidateSignalSeries],
        *,
        min_direction_sources: int = MIN_DIRECTION_SOURCE_QUORUM,
    ) -> TradeSide | None:
        """Resolve the regime direction by majority vote across DIRECTION_SOURCES.

        Previously required ALL direction sources to report and unanimously agree,
        which starved entries whenever any one indicator (e.g. ADX below threshold)
        simply had nothing to say. Now: fail closed if fewer than
        MIN_DIRECTION_SOURCE_QUORUM sources report, and fail closed on an exact tie,
        but otherwise let a clear majority among the sources that did report decide.
        """
        by_source: dict[str, TradeSide] = {}
        for signal in signals:
            source = cls._signal_source(signal.strategy_id)
            if source not in DIRECTION_SOURCES:
                continue
            existing = by_source.get(source)
            if existing is not None and existing != signal.direction:
                return None
            by_source[source] = signal.direction
        if len(by_source) < min_direction_sources:
            return None
        long_votes = sum(1 for direction in by_source.values() if direction == TradeSide.LONG)
        short_votes = sum(1 for direction in by_source.values() if direction == TradeSide.SHORT)
        if long_votes == short_votes:
            return None
        return TradeSide.LONG if long_votes > short_votes else TradeSide.SHORT

    @classmethod
    def _eligible_layered_signals(
        cls,
        signals: list[CandidateSignalSeries],
        *,
        allowed_direction: TradeSide | None,
    ) -> list[CandidateSignalSeries]:
        eligible: list[CandidateSignalSeries] = []
        for signal in signals:
            layer = cls._signal_layer(signal.strategy_id)
            if layer == "direction":
                continue
            if allowed_direction is None:
                if layer == "range":
                    eligible.append(signal)
                continue
            if layer == "entry" and signal.direction == allowed_direction:
                eligible.append(signal)
        return eligible


def _pearson(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    left_values = left[-size:]
    right_values = right[-size:]
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values, strict=True))
    left_var = sum((a - left_mean) ** 2 for a in left_values)
    right_var = sum((b - right_mean) ** 2 for b in right_values)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator else 0.0
