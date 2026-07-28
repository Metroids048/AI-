from __future__ import annotations

from services.execution.tasks import run_market_review


def test_run_market_review_disabled_returns_skip_reason(monkeypatch) -> None:
    monkeypatch.setattr("services.execution.tasks.settings.market_review_enabled", False)

    result = run_market_review()

    assert result == {"called": False, "skip_reason": "MARKET_REVIEW_DISABLED"}
