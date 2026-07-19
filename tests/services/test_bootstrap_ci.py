"""Tests for bootstrap confidence-interval utilities and candidate competition report.

Naming convention: functions containing "bootstrap_ci" are matched by the
``-k bootstrap_ci`` pytest filter used in the task runner command.
Candidate-competition tests contain "candidate_competition".
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from scripts.run_candidate_competition import _SMALL_SAMPLE_THRESHOLD, _markdown
from services.validation.metrics import bootstrap_ci

# ---------------------------------------------------------------------------
# bootstrap_ci unit tests
# ---------------------------------------------------------------------------


def test_bootstrap_ci_returns_zeros_for_empty_pnls() -> None:
    sharpe_ci, expectancy_ci = bootstrap_ci([])
    assert sharpe_ci == (0.0, 0.0)
    assert expectancy_ci == (0.0, 0.0)


def test_bootstrap_ci_returns_zeros_for_single_element_pnls() -> None:
    sharpe_ci, expectancy_ci = bootstrap_ci([0.05])
    assert sharpe_ci == (0.0, 0.0)
    assert expectancy_ci == (0.0, 0.0)


def test_bootstrap_ci_large_sample_narrower_than_small_sample() -> None:
    """90 % CI width decreases as sample size grows (law of large numbers)."""
    rng = random.Random(1)
    large = [rng.gauss(0.008, 0.04) for _ in range(200)]
    small = large[:20]

    sharpe_ci_large, exp_ci_large = bootstrap_ci(large, seed=7)
    sharpe_ci_small, exp_ci_small = bootstrap_ci(small, seed=7)

    large_sharpe_width = sharpe_ci_large[1] - sharpe_ci_large[0]
    small_sharpe_width = sharpe_ci_small[1] - sharpe_ci_small[0]
    assert large_sharpe_width < small_sharpe_width, (
        f"Large-sample Sharpe CI ({large_sharpe_width:.4f}) should be narrower than "
        f"small-sample CI ({small_sharpe_width:.4f})"
    )

    large_exp_width = exp_ci_large[1] - exp_ci_large[0]
    small_exp_width = exp_ci_small[1] - exp_ci_small[0]
    assert large_exp_width < small_exp_width, (
        f"Large-sample expectancy CI ({large_exp_width:.6f}) should be narrower than "
        f"small-sample CI ({small_exp_width:.6f})"
    )


def test_bootstrap_ci_expectancy_ci_contains_sample_mean() -> None:
    """The 90 % expectancy CI should bracket the observed sample mean."""
    rng = random.Random(2)
    pnls = [rng.gauss(0.01, 0.03) for _ in range(100)]
    sample_mean = sum(pnls) / len(pnls)

    _, exp_ci = bootstrap_ci(pnls, seed=42)
    assert exp_ci[0] <= sample_mean <= exp_ci[1], (
        f"Sample mean {sample_mean:.6f} should lie within expectancy CI {exp_ci}"
    )


def test_bootstrap_ci_positive_returns_yield_positive_expectancy_ci_lower_bound() -> None:
    """When all returns are positive the lower bound of the expectancy CI must be positive."""
    pnls = [0.02] * 50  # All identical positive returns.
    _, exp_ci = bootstrap_ci(pnls, seed=0)
    # With all-positive identical values, expectancy = 0.02 and std = 0, so
    # Sharpe is 0.0 but expectancy CI should bracket 0.02.
    assert exp_ci[0] > 0.0, f"Lower expectancy bound should be positive, got {exp_ci[0]}"
    assert exp_ci[1] > 0.0, f"Upper expectancy bound should be positive, got {exp_ci[1]}"


def test_bootstrap_ci_sharpe_ci_is_ordered() -> None:
    """Lower bound must be <= upper bound."""
    rng = random.Random(3)
    pnls = [rng.gauss(0.005, 0.03) for _ in range(80)]
    sharpe_ci, exp_ci = bootstrap_ci(pnls, seed=99)
    assert sharpe_ci[0] <= sharpe_ci[1], f"Sharpe CI not ordered: {sharpe_ci}"
    assert exp_ci[0] <= exp_ci[1], f"Expectancy CI not ordered: {exp_ci}"


def test_bootstrap_ci_is_reproducible_with_same_seed() -> None:
    rng = random.Random(10)
    pnls = [rng.gauss(0.007, 0.035) for _ in range(60)]
    result_a = bootstrap_ci(pnls, seed=42)
    result_b = bootstrap_ci(pnls, seed=42)
    assert result_a == result_b


def test_bootstrap_ci_differs_with_different_seeds() -> None:
    rng = random.Random(10)
    pnls = [rng.gauss(0.007, 0.035) for _ in range(60)]
    result_a = bootstrap_ci(pnls, seed=1)
    result_b = bootstrap_ci(pnls, seed=999)
    # Different seeds almost certainly produce different samples.
    assert result_a != result_b


# ---------------------------------------------------------------------------
# candidate_competition: _markdown() tests
# ---------------------------------------------------------------------------


def _make_row(
    *,
    candidate_id: str = "test_strat",
    symbol: str = "BTC/USDT",
    oos_sample_count: int = 35,
    oos_trade_count: int | None = None,
    net_expectancy: float = 0.006,
    sharpe: float = 1.8,
    profit_factor: float = 1.4,
    max_drawdown: float = 0.15,
    failed_reasons: list[str] | None = None,
) -> dict:
    n = oos_trade_count if oos_trade_count is not None else oos_sample_count
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "sample_count": oos_sample_count * 3,
        "oos_sample_count": oos_sample_count,
        "oos_trade_count": n,
        "win_rate": 0.45,
        "net_expectancy": net_expectancy,
        "sharpe": sharpe,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "sharpe_ci_90": [sharpe - 0.3, sharpe + 0.3],
        "expectancy_ci_90": [net_expectancy - 0.002, net_expectancy + 0.002],
        "failed_reasons": failed_reasons or [],
        "data_issues": [],
        "evaluation_start": "2026-03-01T00:00:00+00:00",
        "evaluation_end": "2026-07-01T00:00:00+00:00",
    }


def test_candidate_competition_markdown_small_sample_warning_present_when_below_threshold() -> None:
    """When oos_trade_count < 30 the markdown must contain the small-sample warning."""
    n_small = _SMALL_SAMPLE_THRESHOLD - 1  # exactly 29
    rows = [_make_row(oos_trade_count=n_small, candidate_id="low_n_strat")]
    md = _markdown(rows, generated_at=datetime(2026, 7, 19, tzinfo=UTC), days=180)

    assert "小样本警告" in md, "Markdown should contain small-sample warning text"
    assert str(n_small) in md, f"Markdown should mention the trade count {n_small}"
    assert "low_n_strat" in md


def test_candidate_competition_markdown_no_warning_when_oos_above_threshold() -> None:
    """When oos_trade_count >= 30 no small-sample warning section should appear."""
    rows = [_make_row(oos_trade_count=_SMALL_SAMPLE_THRESHOLD)]
    md = _markdown(rows, generated_at=datetime(2026, 7, 19, tzinfo=UTC), days=180)
    assert "小样本警告" not in md


def test_candidate_competition_markdown_ci_columns_present() -> None:
    """The markdown table must include Sharpe CI and expectancy CI columns."""
    rows = [_make_row()]
    md = _markdown(rows, generated_at=datetime(2026, 7, 19, tzinfo=UTC), days=180)
    assert "Sharpe CI 90%" in md, "Markdown table header should include 'Sharpe CI 90%'"
    assert "expectancy CI 90%" in md, "Markdown table header should include 'expectancy CI 90%'"


def test_candidate_competition_markdown_mixed_sample_sizes_warns_only_small() -> None:
    """Only rows with oos_trade_count < 30 get the warning; large rows are silent."""
    rows = [
        _make_row(candidate_id="big_strat", oos_trade_count=60),
        _make_row(candidate_id="tiny_strat", oos_trade_count=10),
    ]
    md = _markdown(rows, generated_at=datetime(2026, 7, 19, tzinfo=UTC), days=365)

    assert "tiny_strat" in md
    assert "10" in md  # trade count in the warning line
    # The big strategy should not generate a warning line (though it appears in the table).
    warning_section = md.split("小样本警告")[-1] if "小样本警告" in md else ""
    assert "big_strat" not in warning_section


# ---------------------------------------------------------------------------
# Pass/fail logic stability: adding CI fields must not change failed_reasons
# ---------------------------------------------------------------------------


def _make_oos_metrics_stub(
    *,
    sharpe: float,
    profit_factor: float,
    max_drawdown: float,
    net_expectancy: float,
    total_trades: int = 35,
) -> object:
    """Lightweight stub that satisfies evidence_failure_reasons() expectations."""
    from unittest.mock import MagicMock

    stub = MagicMock()
    stub.sharpe = sharpe
    stub.profit_factor = profit_factor
    stub.max_drawdown = max_drawdown
    stub.net_expectancy = net_expectancy
    stub.total_trades = total_trades
    stub.win_rate = 0.4
    stub.signal_count = total_trades
    stub.data_issues = []
    stub.evaluation_start = datetime(2026, 1, 1, tzinfo=UTC)
    stub.evaluation_end = datetime(2026, 7, 1, tzinfo=UTC)

    # Build synthetic trades so bootstrap_ci can sample from them.
    closed_at_base = datetime(2026, 4, 1, tzinfo=UTC)
    trades = []
    for i in range(total_trades):
        t = MagicMock()
        t.net_return = net_expectancy
        t.closed_at = closed_at_base + timedelta(days=i)
        trades.append(t)
    stub.trades = tuple(trades)
    return stub


def test_bootstrap_ci_does_not_change_pass_fail_logic_for_passing_row() -> None:
    """A row that passes all gates must still pass after CI fields are added."""
    from scripts.run_candidate_competition import _row

    oos = _make_oos_metrics_stub(sharpe=2.0, profit_factor=1.5, max_drawdown=0.15, net_expectancy=0.008)
    full = _make_oos_metrics_stub(sharpe=2.0, profit_factor=1.5, max_drawdown=0.15, net_expectancy=0.008)

    row = _row(candidate_id="pass_strat", symbol="BTC/USDT", full=full, oos=oos)

    assert row["failed_reasons"] == [], f"Expected no failed reasons, got {row['failed_reasons']}"
    # CI fields are present and well-formed.
    assert len(row["sharpe_ci_90"]) == 2
    assert len(row["expectancy_ci_90"]) == 2
    assert row["sharpe_ci_90"][0] <= row["sharpe_ci_90"][1]
    assert row["expectancy_ci_90"][0] <= row["expectancy_ci_90"][1]
    assert "oos_trade_count" in row


def test_bootstrap_ci_does_not_change_pass_fail_logic_for_failing_row() -> None:
    """A row that fails gates must still fail after CI fields are added."""
    from scripts.run_candidate_competition import _row

    oos = _make_oos_metrics_stub(
        sharpe=0.5,  # below min_sharpe=1.0
        profit_factor=1.1,  # below min_profit_factor=1.3
        max_drawdown=0.30,  # above max_drawdown=0.25
        net_expectancy=-0.002,  # non-positive
        total_trades=20,
    )
    full = _make_oos_metrics_stub(sharpe=0.5, profit_factor=1.1, max_drawdown=0.30, net_expectancy=-0.002)

    row = _row(candidate_id="fail_strat", symbol="ETH/USDT", full=full, oos=oos)

    assert len(row["failed_reasons"]) > 0, "Expected at least one failed reason for a clearly failing strategy"
    # CI fields should still be present and ordered.
    assert row["sharpe_ci_90"][0] <= row["sharpe_ci_90"][1]
    assert row["expectancy_ci_90"][0] <= row["expectancy_ci_90"][1]
