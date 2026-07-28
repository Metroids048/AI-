"""Task 15: Shadow run tests (plan section 15 / Gate 15).

Gate 15:
- Shadow submits nothing: network_order_submit_calls == 0.
- Every bar is explainable (a funnel payload is always produced).
- No unhandled exception escapes the run.
- The guard adapter turns "shadow means no writes" into an enforced invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from scripts.v2_shadow_run import SubmissionGuardAdapter, shadow_run
from services.automated_trading.application.decision_service import BarView, TimeframeView

BAR_START = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)


def _timeframe(closes: list[float]) -> TimeframeView:
    bars = tuple(
        BarView(
            timestamp=BAR_START + timedelta(minutes=15 * i),
            open=Decimal(str(c)),
            high=Decimal(str(c)) * Decimal("1.003"),
            low=Decimal(str(c)) * Decimal("0.997"),
            close=Decimal(str(c)),
            volume=Decimal("100"),
        )
        for i, c in enumerate(closes)
    )
    return TimeframeView(timeframe="15m", bars=bars)


def _healthy_snapshot():
    from services.automated_trading.infrastructure.market_snapshot_provider import (
        AuthoritativeAccountSnapshot,
    )

    return AuthoritativeAccountSnapshot(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        positions=[],
        pending_orders=[],
        snapshot_timestamp=datetime.now(UTC),
    )


def _inner_adapter() -> MagicMock:
    inner = MagicMock()
    inner.fetch_authoritative_snapshot.return_value = _healthy_snapshot()
    inner.fetch_market_snapshot.return_value = MagicMock(
        current_price=Decimal("101.0"),
        atr=Decimal("0"),
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    return inner


# ---------------------------------------------------------------------------
# SubmissionGuardAdapter
# ---------------------------------------------------------------------------


class TestSubmissionGuardAdapter:
    def test_read_paths_pass_through(self) -> None:
        inner = _inner_adapter()
        guard = SubmissionGuardAdapter(inner)

        guard.fetch_authoritative_snapshot()
        inner.fetch_authoritative_snapshot.assert_called_once()

    def test_submit_market_order_raises(self) -> None:
        guard = SubmissionGuardAdapter(_inner_adapter())
        with pytest.raises(AssertionError, match="SHADOW contract violation"):
            guard.submit_market_order()

    def test_submit_protection_raises(self) -> None:
        guard = SubmissionGuardAdapter(_inner_adapter())
        with pytest.raises(AssertionError, match="SHADOW contract violation"):
            guard.submit_protection()

    def test_submit_reduce_only_exit_raises(self) -> None:
        guard = SubmissionGuardAdapter(_inner_adapter())
        with pytest.raises(AssertionError, match="SHADOW contract violation"):
            guard.submit_reduce_only_exit()

    def test_cancel_order_raises(self) -> None:
        guard = SubmissionGuardAdapter(_inner_adapter())
        with pytest.raises(AssertionError, match="SHADOW contract violation"):
            guard.cancel_order()

    def test_submit_attempts_counted(self) -> None:
        guard = SubmissionGuardAdapter(_inner_adapter())
        for _ in range(3):
            with pytest.raises(AssertionError):
                guard.submit_market_order()
        assert guard.submit_attempts == 3

    def test_inner_write_never_reached(self) -> None:
        inner = _inner_adapter()
        guard = SubmissionGuardAdapter(inner)
        with pytest.raises(AssertionError):
            guard.submit_market_order()
        inner.submit_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# shadow_run
# ---------------------------------------------------------------------------


class TestShadowRun:
    def test_clean_run_returns_zero(self) -> None:
        """A flat market produces no candidate, no submission, HEALTHY recon."""
        flat = _timeframe([100.0] * 50)

        with (
            patch("scripts.v2_shadow_run._build_adapter", return_value=_inner_adapter()),
            patch("scripts.v2_shadow_run._load_timeframe", return_value=flat),
        ):
            code = shadow_run(symbols=["BTC/USDT"], cycles=1)

        assert code == 0

    def test_market_data_failure_returns_one(self) -> None:
        with (
            patch("scripts.v2_shadow_run._build_adapter", return_value=_inner_adapter()),
            patch("scripts.v2_shadow_run._load_timeframe", side_effect=RuntimeError("no ohlcv")),
        ):
            code = shadow_run(symbols=["BTC/USDT"], cycles=1)

        assert code == 1

    def test_signal_bars_still_never_submit(self) -> None:
        """Even with a live LONG signal, SHADOW must not reach the exchange."""
        rising = _timeframe(
            [100.0] * 30
            + [
                100.3,
                100.1,
                100.4,
                100.2,
                100.5,
                100.3,
                100.6,
                100.4,
                100.7,
                100.5,
                100.8,
                100.6,
                100.9,
                100.7,
                101.0,
                100.8,
                101.1,
                100.9,
                101.2,
                101.0,
            ]
        )
        inner = _inner_adapter()

        with (
            patch("scripts.v2_shadow_run._build_adapter", return_value=inner),
            patch("scripts.v2_shadow_run._load_timeframe", return_value=rising),
        ):
            shadow_run(symbols=["BTC/USDT"], cycles=1)

        # The real proof: the underlying adapter's write path was never touched.
        inner.submit_market_order.assert_not_called()
        inner.submit_protection.assert_not_called()

    def test_multiple_cycles_and_symbols(self) -> None:
        flat = _timeframe([100.0] * 50)

        with (
            patch("scripts.v2_shadow_run._build_adapter", return_value=_inner_adapter()),
            patch("scripts.v2_shadow_run._load_timeframe", return_value=flat) as loader,
        ):
            code = shadow_run(symbols=["BTC/USDT", "ETH/USDT"], cycles=2)

        assert code == 0
        # 2 cycles x 2 symbols
        assert loader.call_count == 4

    def test_snapshot_failure_reports_unavailable_not_crash(self) -> None:
        """A dead gateway yields UNAVAILABLE reconciliation, not an exception."""
        inner = _inner_adapter()
        inner.fetch_authoritative_snapshot.side_effect = RuntimeError("gateway down")
        flat = _timeframe([100.0] * 50)

        with (
            patch("scripts.v2_shadow_run._build_adapter", return_value=inner),
            patch("scripts.v2_shadow_run._load_timeframe", return_value=flat),
        ):
            code = shadow_run(symbols=["BTC/USDT"], cycles=1)

        # Cycle records an error rather than raising -> exit 1, no traceback.
        assert code == 1
