"""SignalEnsemble and MetaLabel business logic."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from statistics import mean

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


class SignalEnsembleService:
    """Fuse low-correlation signal candidates into a single trade candidate."""

    def create_ensemble(self, request: SignalEnsembleRequest) -> SignalEnsemble:
        if not request.signals:
            raise ValueError("at least one signal is required")
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
        if not votes:
            return SignalEnsemble(
                ensemble_id=str(uuid.uuid4()),
                strategy_refs=[signal.strategy_id for signal in request.signals],
                fusion_method=request.fusion_method,
                raw_votes=[],
                ensemble_status=EnsembleStatus.DISCARDED_LOW_CONFIDENCE,
                correlation_matrix_ref="all_candidates_filtered",
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
        audit = {
            "correlation_threshold": request.correlation_threshold,
            "min_history": request.min_history,
            "input_count": len(request.signals),
            "kept_count": len(votes),
        }
        return SignalEnsemble(
            ensemble_id=str(uuid.uuid4()),
            strategy_refs=[vote.strategy_id for vote in votes],
            fusion_method=request.fusion_method,
            correlation_matrix_ref=f"correlation_filter:{audit}",
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
        wins = [value for value in returns if value > 0]
        win_rate = len(wins) / len(returns) if returns else 0.0
        average_return = mean(returns) if returns else 0.0
        bet_taken = win_rate >= request.min_win_rate and average_return > request.min_average_return
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
            model_ref="rule_meta_label_v1",
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
