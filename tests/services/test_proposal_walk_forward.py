from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.validation.proposal_walk_forward import (
    build_proposal_walk_forward_windows,
    record_walk_forward_trials,
)
from services.validation.strategy_promotion import TrialLedger


def test_phase1_walk_forward_has_fixed_training_purge_oos_and_embargo_boundaries(tmp_path: Path) -> None:
    windows = build_proposal_walk_forward_windows(
        development_start=datetime(2023, 1, 29, tzinfo=UTC),
        development_end=datetime(2026, 1, 29, tzinfo=UTC),
    )

    assert len(windows) == 8
    assert windows[0].train_start == datetime(2023, 1, 29, tzinfo=UTC)
    assert windows[0].oos_start == datetime(2024, 1, 29, tzinfo=UTC)
    assert windows[-1].oos_end == datetime(2026, 1, 29, tzinfo=UTC)
    assert windows[0].purge_end - windows[0].purge_start == timedelta(minutes=(72 + 8) * 15)
    assert windows[0].embargo_end - windows[0].embargo_start == timedelta(hours=24)
    assert windows[-1].embargo_end - windows[-1].embargo_start == timedelta(hours=24)

    ledger = TrialLedger(tmp_path / "trial-ledger.jsonl")
    metrics = {
        (window.window_id, candidate): {"total_trades": 0} for window in windows for candidate in ("a", "b", "c")
    }
    record_walk_forward_trials(
        ledger,
        windows=windows,
        candidate_ids=("a", "b", "c"),
        metrics_by_trial=metrics,
    )
    assert len(ledger.read_all()) == 24
    assert ledger.read_all()[0]["parameters"]["metrics"] == {"total_trades": 0}
    record_walk_forward_trials(ledger, windows=windows, candidate_ids=("a",))
    assert len(ledger.read_all()) == 32


def test_walk_forward_rejects_short_range_or_insufficient_embargo() -> None:
    with pytest.raises(ValueError, match="eight complete"):
        build_proposal_walk_forward_windows(
            development_start=datetime(2023, 1, 29, tzinfo=UTC),
            development_end=datetime(2025, 1, 29, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="at least 24 hours"):
        build_proposal_walk_forward_windows(
            development_start=datetime(2023, 1, 29, tzinfo=UTC),
            development_end=datetime(2026, 1, 29, tzinfo=UTC),
            embargo=timedelta(hours=23),
        )
