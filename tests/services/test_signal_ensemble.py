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
            MetaLabelSample(sample_time=signal_time - timedelta(days=index + 1), net_return=0.01)
            for index in range(20)
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


def test_meta_label_rejects_cold_start_even_when_short_history_is_positive() -> None:
    service = SignalEnsembleService()
    signal_time = datetime(2024, 2, 1, tzinfo=UTC)
    label = service.create_meta_label(
        MetaLabelRequest(
            ensemble_id="ensemble-cold-start",
            signal_time=signal_time,
            training_samples=[
                MetaLabelSample(sample_time=signal_time - timedelta(days=index + 1), net_return=0.05)
                for index in range(4)
            ],
        )
    )

    assert label.bet_decision.value == "bet_skipped"
    assert label.position_size_fraction is None


def _layered_signal(
    strategy_id: str,
    *,
    direction: str,
    weight: float = 1.0,
    confidence: float = 0.8,
) -> dict:
    series = [float(i % 7) for i in range(220)]
    return {
        "strategy_id": strategy_id,
        "direction": direction,
        "weight": weight,
        "confidence": confidence,
        "validation_score": confidence,
        "series": series,
    }


def test_layered_fusion_resolves_direction_by_majority_vote_not_unanimity() -> None:
    """The direction gate used to require ALL direction sources to unanimously
    agree, which starved entries whenever a single indicator disagreed (e.g. ADX
    lagging). It now resolves by majority (2-of-3 here), so a lone dissenting
    direction source no longer blocks the whole ensemble.
    """
    request = SignalEnsembleRequest(
        fusion_method="layered_regime_entry",
        min_history=200,
        signals=[
            _layered_signal("technical_dow_trend:long", direction="long"),
            _layered_signal("technical_ema_trend:long", direction="long"),
            _layered_signal("technical_adx:short", direction="short"),
            _layered_signal("technical_macd:long", direction="long"),
            _layered_signal("technical_rsi:long", direction="long"),
            _layered_signal("price_action_pin_bar:long", direction="long"),
        ],
    )

    ensemble = SignalEnsembleService().create_ensemble(request)

    assert ensemble.fused_direction == "long"
    assert ensemble.ensemble_status.value == "passed_to_meta_label"
    assert "allowed_direction=long" in (ensemble.correlation_matrix_ref or "")


def test_layered_fusion_fails_closed_on_exact_direction_tie() -> None:
    """With mtf_ma added as a 4th direction source, a 2-2 tie must still fail
    closed rather than arbitrarily picking a side.
    """
    request = SignalEnsembleRequest(
        fusion_method="layered_regime_entry",
        min_history=200,
        signals=[
            _layered_signal("technical_dow_trend:long", direction="long"),
            _layered_signal("technical_ema_trend:long", direction="long"),
            _layered_signal("technical_adx:short", direction="short"),
            _layered_signal("technical_mtf_ma:short", direction="short"),
            _layered_signal("technical_macd:long", direction="long"),
        ],
    )

    ensemble = SignalEnsembleService().create_ensemble(request)

    assert ensemble.fused_direction is None
    assert ensemble.ensemble_status.value == "discarded_low_confidence"
    assert ensemble.raw_votes == []
    assert "allowed_direction=none" in (ensemble.correlation_matrix_ref or "")


def test_layered_fusion_fails_closed_when_direction_sources_below_quorum() -> None:
    """Only 2 of the possible direction sources reporting must still fail closed
    even if those 2 agree, since MIN_DIRECTION_SOURCE_QUORUM is 3.
    """
    request = SignalEnsembleRequest(
        fusion_method="layered_regime_entry",
        min_history=200,
        signals=[
            _layered_signal("technical_dow_trend:long", direction="long"),
            _layered_signal("technical_ema_trend:long", direction="long"),
            _layered_signal("technical_macd:long", direction="long"),
        ],
    )

    ensemble = SignalEnsembleService().create_ensemble(request)

    assert ensemble.fused_direction is None
    assert ensemble.ensemble_status.value == "discarded_low_confidence"


def test_layered_fusion_ignores_counter_trend_entry_votes() -> None:
    request = SignalEnsembleRequest(
        fusion_method="layered_regime_entry",
        min_history=200,
        signals=[
            _layered_signal("technical_dow_trend:long", direction="long"),
            _layered_signal("technical_ema_trend:long", direction="long"),
            _layered_signal("technical_adx:long", direction="long"),
            _layered_signal("technical_macd:long", direction="long", confidence=0.9),
            _layered_signal("technical_rsi:short", direction="short", confidence=0.95),
            _layered_signal("price_action_false_breakout:short", direction="short", confidence=0.95),
            _layered_signal("technical_vwap:short", direction="short", confidence=0.99),
        ],
    )

    ensemble = SignalEnsembleService().create_ensemble(request)

    assert ensemble.fused_direction == "long"
    assert {vote.strategy_id for vote in ensemble.raw_votes} == {"technical_macd:long"}
    assert "allowed_direction=long" in (ensemble.correlation_matrix_ref or "")
