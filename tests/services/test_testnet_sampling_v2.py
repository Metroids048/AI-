"""Task 11: Testnet Sampling Lane — sampling_service + testnet_sampling_v2 tests.

Gate 11:
- System can produce sampling candidates from signal rules.
- non_promotable is always True on TESTNET_SAMPLING candidates.
- Cooldown, per-symbol cap, and daily total cap all fire correctly.
- Insufficient bars returns None (no crash).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.application.sampling_service import (
    SamplingState,
    check_sampling_throttle,
)
from services.automated_trading.domain.candidates import CandidateLane
from services.strategy_library.candidates.testnet_sampling_v2 import generate_sampling_candidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BAR_START = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
SYMBOL = "BTC/USDT"
CYCLE_ID = "cycle-sampling-test"


def _make_bars(closes: list[float]) -> TimeframeView:
    bars = []
    for i, c in enumerate(closes):
        p = Decimal(str(c))
        bars.append(
            BarView(
                timestamp=BAR_START + timedelta(minutes=15 * i),
                open=p,
                high=p * Decimal("1.005"),
                low=p * Decimal("0.995"),
                close=p,
                volume=Decimal("100"),
            )
        )
    return TimeframeView(timeframe="15m", bars=tuple(bars))


def _long_signal_bars() -> TimeframeView:
    """50 bars showing a rising trend above EMA50 with MACD > 0 and RSI ~55."""
    closes = [100.0] * 30 + [
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
    return _make_bars(closes)


def _flat_bars() -> TimeframeView:
    return _make_bars([100.0] * 50)


# ---------------------------------------------------------------------------
# generate_sampling_candidate
# ---------------------------------------------------------------------------


class TestGenerateSamplingCandidate:
    def test_returns_none_for_insufficient_bars(self) -> None:
        tf = _make_bars([100.0] * 10)  # not enough for EMA50
        now = BAR_START + timedelta(minutes=15 * 10)
        result = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)
        assert result is None

    def test_returns_none_for_flat_market(self) -> None:
        tf = _flat_bars()
        now = BAR_START + timedelta(minutes=15 * 50)
        result = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)
        assert result is None

    def test_returns_none_for_empty_timeframe(self) -> None:
        tf = TimeframeView(timeframe="15m", bars=())
        now = datetime.now(UTC)
        result = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)
        assert result is None

    def test_returns_candidate_for_long_signal(self) -> None:
        tf = _long_signal_bars()
        now = BAR_START + timedelta(minutes=15 * 49) + timedelta(seconds=10)
        result = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)

        assert result is not None
        assert result.side == "LONG"
        assert result.symbol == SYMBOL
        assert result.lane == CandidateLane.TESTNET_SAMPLING
        assert result.non_promotable is True

    def test_candidate_always_non_promotable(self) -> None:
        tf = _long_signal_bars()
        now = BAR_START + timedelta(minutes=15 * 49) + timedelta(seconds=10)
        candidate = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)

        assert candidate is not None
        assert candidate.non_promotable is True

    def test_candidate_stop_distance_positive(self) -> None:
        tf = _long_signal_bars()
        now = BAR_START + timedelta(minutes=15 * 49) + timedelta(seconds=10)
        candidate = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)

        assert candidate is not None
        assert candidate.stop_distance > 0

    def test_candidate_take_profit_is_1_5_times_stop(self) -> None:
        tf = _long_signal_bars()
        now = BAR_START + timedelta(minutes=15 * 49) + timedelta(seconds=10)
        candidate = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)

        assert candidate is not None
        assert candidate.take_profit_distance is not None
        ratio = candidate.take_profit_distance / candidate.stop_distance
        assert abs(ratio - Decimal("1.5")) < Decimal("0.001")

    def test_candidate_expires_after_ttl(self) -> None:
        tf = _long_signal_bars()
        now = BAR_START + timedelta(minutes=15 * 49) + timedelta(seconds=10)
        candidate = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now, candidate_ttl_seconds=75)
        assert candidate is not None
        expected_expiry = now + timedelta(seconds=75)
        diff = abs((candidate.expires_at - expected_expiry).total_seconds())
        assert diff < 1

    def test_candidate_strategy_id_is_testnet_sampling_v2(self) -> None:
        tf = _long_signal_bars()
        now = BAR_START + timedelta(minutes=15 * 49) + timedelta(seconds=10)
        candidate = generate_sampling_candidate(CYCLE_ID, SYMBOL, tf, now)

        assert candidate is not None
        assert candidate.strategy_id == "testnet_sampling_v2"


# ---------------------------------------------------------------------------
# SamplingState + check_sampling_throttle
# ---------------------------------------------------------------------------


class TestSamplingThrottle:
    def test_new_state_allows_entry(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
        result = check_sampling_throttle(SYMBOL, now, state)
        assert result.allowed

    def test_cooldown_blocks_immediate_retry(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
        state.record_entry(SYMBOL, now)
        # 5 seconds later — well within the 900s cooldown
        result = check_sampling_throttle(SYMBOL, now + timedelta(seconds=5), state, cooldown_seconds=900)
        assert not result.allowed
        assert "cooldown" in result.reason

    def test_cooldown_clears_after_elapsed(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
        state.record_entry(SYMBOL, now)
        after = now + timedelta(seconds=901)
        result = check_sampling_throttle(SYMBOL, after, state, cooldown_seconds=900)
        assert result.allowed

    def test_daily_per_symbol_cap(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
        for i in range(3):
            state.record_entry(SYMBOL, now + timedelta(hours=i))

        result = check_sampling_throttle(
            SYMBOL,
            now + timedelta(hours=4),
            state,
            max_daily_trades_per_symbol=3,
            cooldown_seconds=0,
        )
        assert not result.allowed
        assert "daily sampling cap" in result.reason

    def test_daily_total_cap(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
        for i in range(6):
            state.record_entry(f"SYM{i}/USDT", now + timedelta(hours=i))

        result = check_sampling_throttle(
            "BTC/USDT",
            now + timedelta(hours=7),
            state,
            max_daily_trades_total=6,
            cooldown_seconds=0,
        )
        assert not result.allowed
        assert "total daily" in result.reason

    def test_daily_reset_after_midnight(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 23, 0, tzinfo=UTC)
        for _ in range(3):
            state.record_entry(SYMBOL, now)

        # New day
        next_day = datetime(2026, 7, 29, 0, 5, tzinfo=UTC)
        result = check_sampling_throttle(
            SYMBOL,
            next_day,
            state,
            max_daily_trades_per_symbol=3,
            cooldown_seconds=0,
        )
        assert result.allowed

    def test_record_entry_increments_counts(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
        state.record_entry(SYMBOL, now)
        assert state.daily_symbol_counts[SYMBOL] == 1
        assert state.daily_total_count == 1
        assert state.last_entry_at[SYMBOL] == now

    def test_different_symbols_independent_cooldown(self) -> None:
        state = SamplingState()
        now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
        state.record_entry("BTC/USDT", now)
        # ETH should not be in cooldown
        result = check_sampling_throttle("ETH/USDT", now + timedelta(seconds=5), state)
        assert result.allowed
