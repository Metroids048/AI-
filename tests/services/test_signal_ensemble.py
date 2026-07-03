from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.strategy_library.ensemble import SignalEnsembleService
from shared.models import MetaLabelRequest, MetaLabelSample, SignalEnsembleRequest


def test_high_correlation_weaker_signal_is_downweighted() -> None:
    series = [float(i % 7) for i in range(220)]
    request = SignalEnsembleRequest(
        min_history=200,
        signals=[
            {
                "strategy_id": "alpha-good",
                "direction": "long",
                "weight": 1.0,
                "confidence": 0.8,
                "validation_score": 1.2,
                "series": series,
            },
            {
                "strategy_id": "alpha-weak",
                "direction": "long",
                "weight": 1.0,
                "confidence": 0.8,
                "validation_score": 0.4,
                "series": [value * 2 for value in series],
            },
            {
                "strategy_id": "short-diversifier",
                "direction": "short",
                "weight": 0.5,
                "confidence": 0.7,
                "validation_score": 0.9,
                "series": [float((i * 3) % 11) for i in range(220)],
            },
        ],
    )

    ensemble = SignalEnsembleService().create_ensemble(request)

    weights = {vote.strategy_id: vote.weight for vote in ensemble.raw_votes}
    assert weights["alpha-good"] == 1.0
    assert weights["alpha-weak"] == 0.25
    assert ensemble.fused_direction == "long"


def test_meta_label_takes_bet_for_positive_history_and_rejects_future_samples() -> None:
    service = SignalEnsembleService()
    signal_time = datetime(2024, 2, 1, tzinfo=UTC)
    request = MetaLabelRequest(
        ensemble_id="ensemble-1",
        signal_time=signal_time,
        training_samples=[
            MetaLabelSample(sample_time=signal_time - timedelta(days=10), net_return=0.03),
            MetaLabelSample(sample_time=signal_time - timedelta(days=9), net_return=0.01),
            MetaLabelSample(sample_time=signal_time - timedelta(days=8), net_return=-0.005),
            MetaLabelSample(sample_time=signal_time - timedelta(days=7), net_return=0.02),
        ],
    )

    label = service.create_meta_label(request)

    assert label.bet_decision == "bet_taken"
    assert label.position_size_fraction is not None
    with pytest.raises(ValueError, match="earlier than signal_time"):
        service.create_meta_label(
            MetaLabelRequest(
                ensemble_id="ensemble-1",
                signal_time=signal_time,
                training_samples=[MetaLabelSample(sample_time=signal_time + timedelta(hours=1), net_return=0.1)],
            )
        )
