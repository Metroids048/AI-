"""AI review must not spend the entry's price-drift budget.

Runtime evidence 2026-08-07 (armed v2_active, ETH/USDT bar 09:45):
the advisory AI review spent 5.50s of a 6.23s funnel returning
``AI_PROVIDER_UNAVAILABLE`` through a 4-candidate provider chain, and the entry
was then rejected at ``drift_bps=29.32`` against ``drift_ceiling_bps=20``.

The review is advisory — a failure yields a SKIPPED stage and never blocks the
entry — so its latency must be bounded. These tests lock that bound.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.automated_trading.application import cycle_service
from services.automated_trading.application.cycle_service import (
    CycleRequest,
    _run_trade_review_budgeted,
)
from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.runtime_lock import EngineActivation


class _Candidate:
    candidate_id = "candidate-under-test"
    strategy_id = "strategy-under-test"
    strategy_version = "1.0.0"
    lane = "TESTNET_SAMPLING"
    side = "LONG"
    confidence = Decimal("0.5")
    signal_reference_price = Decimal("1909.91")
    stop_distance = Decimal("6.684685")
    take_profit_distance = Decimal("10.0270275")


def _bar(ts: datetime) -> BarView:
    return BarView(
        timestamp=ts,
        open=Decimal("1900"),
        high=Decimal("1910"),
        low=Decimal("1899"),
        close=Decimal("1909.91"),
        volume=Decimal("10"),
    )


def _request(**overrides) -> CycleRequest:  # noqa: ANN003
    now = datetime(2026, 8, 7, 9, 45, 32, tzinfo=UTC)
    base = {
        "cycle_id": "cycle-under-test",
        "symbol": "ETH/USDT",
        "timeframe": "15m",
        "entry_timeframe": TimeframeView(timeframe="15m", bars=(_bar(now),)),
        "execution_mode": V2ExecutionMode.BINANCE_TESTNET,
        "engine_activation": EngineActivation.ACTIVE,
        "fencing_token": "token-under-test",
        "now": now,
        "persist_facts": True,
    }
    base.update(overrides)
    return CycleRequest(**base)


def test_default_budget_is_well_under_a_typical_drift_window() -> None:
    """A default cycle must not allow the advisory review to eat seconds."""
    assert _request().ai_review_budget_seconds <= 2.0


def test_review_exceeding_budget_is_abandoned_and_reported_as_skipped(monkeypatch) -> None:
    """A hung provider chain must be abandoned, not awaited."""

    def _hang(request, candidate):  # noqa: ANN001, ARG001
        time.sleep(30)
        raise AssertionError("budgeted review must not wait for a hung provider")

    monkeypatch.setattr(cycle_service, "_run_trade_review", _hang)

    started = time.monotonic()
    review = _run_trade_review_budgeted(_request(ai_review_budget_seconds=0.2), _Candidate())
    elapsed = time.monotonic() - started

    assert review["status"] == "skipped"
    assert "budget_exceeded" in str(review["error"])
    # The whole point: the caller regains control near the budget, not after the
    # provider chain finishes.
    assert elapsed < 5.0


def test_fast_successful_review_is_passed_through_untouched(monkeypatch) -> None:
    """Budgeting must not discard a provider opinion that arrives in time."""
    expected = {
        "status": "passed",
        "provider": "anthropic",
        "model": "claude-test",
        "result": {"verdict": "APPROVE"},
        "error": None,
    }
    monkeypatch.setattr(cycle_service, "_run_trade_review", lambda request, candidate: dict(expected))

    review = _run_trade_review_budgeted(_request(ai_review_budget_seconds=5.0), _Candidate())

    assert review == expected


def test_provider_error_inside_budget_is_surfaced_not_masked(monkeypatch) -> None:
    """A fast, genuine provider failure must keep its error for the funnel trace."""

    def _boom(request, candidate):  # noqa: ANN001, ARG001
        raise RuntimeError("provider 404")

    monkeypatch.setattr(cycle_service, "_run_trade_review", _boom)

    review = _run_trade_review_budgeted(_request(ai_review_budget_seconds=5.0), _Candidate())

    assert review["status"] == "error"
    assert "provider 404" in str(review["error"])


def test_zero_budget_disables_the_advisory_call_entirely(monkeypatch) -> None:
    """Operators must be able to remove the hot-path call, not just shorten it."""

    def _must_not_run(request, candidate):  # noqa: ANN001, ARG001
        raise AssertionError("a zero budget must not invoke any provider")

    monkeypatch.setattr(cycle_service, "_run_trade_review", _must_not_run)

    review = _run_trade_review_budgeted(_request(ai_review_budget_seconds=0.0), _Candidate())

    assert review["status"] == "skipped"
    assert review["error"] == "ai_review_budget_disabled"


@pytest.mark.parametrize("budget", [0.0, 0.2, 1.5, 5.0])
def test_budgeted_review_never_raises_into_the_entry_path(monkeypatch, budget: float) -> None:
    """Advisory failures must never propagate as exceptions to the entry cycle."""

    def _boom(request, candidate):  # noqa: ANN001, ARG001
        raise RuntimeError("chain exhausted")

    monkeypatch.setattr(cycle_service, "_run_trade_review", _boom)

    review = _run_trade_review_budgeted(_request(ai_review_budget_seconds=budget), _Candidate())

    assert review["status"] in {"skipped", "error"}
