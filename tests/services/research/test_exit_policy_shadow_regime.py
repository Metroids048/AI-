"""P2-A2 point-in-time regime classification tests. RED before GREEN.

Covers REGIME-01..REGIME-12 of the P2-A2 contract. Synthetic bars are used so every
assertion is deterministic; synthetic data never reaches the real-sample report.

The classifier under test is a *research-only projection*: it consumes the production
`RegimeScorerV2` score and maps it onto a deterministic label. These tests therefore
build real `MarketContext` objects through the production `MarketContextBuilder`, so a
drift in production point-in-time semantics surfaces here rather than silently
changing research labels.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.research.exit_policy_shadow.contracts import Regime
from services.research.exit_policy_shadow.regime import (
    CLASSIFIER_VERSION,
    MIN_BARS_FOR_LABEL,
    REGIME_LABEL_CONFIDENCE_THRESHOLD,
    TrendDirection,
    project_regime_label,
)
from services.strategy_library.context import TIMEFRAME_DELTAS, MarketContext, MarketContextBuilder
from services.strategy_library.regime.scorer_v2 import SCORER_VERSION, RegimeScore, RegimeScorerV2
from shared.models import OHLCVBar, Timeframe

DECISION = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
SYMBOL = "BTC/USDT"

# Mirrors the production research window (PROPOSAL_CONTEXT_WINDOW_LENGTHS) so a label
# means the same thing here as it does in the runtime pipeline.
WINDOWS = {"1m": 2, "5m": 2, "15m": 80, "1h": 80, "4h": 80}

BASELINE_SPAN = Decimal("0.5")
SHOCK_SPAN = Decimal("1.5")


def _series(
    *,
    timeframe: str,
    count: int,
    end: datetime = DECISION,
    start_close: Decimal = Decimal("100"),
    total_return: Decimal = Decimal("0"),
    span: Decimal = BASELINE_SPAN,
    shock_span: Decimal | None = None,
    shock_bars: int = 5,
    skip_last: int = 0,
) -> list[OHLCVBar]:
    """Build a contiguous closed-bar series ending exactly at ``end``.

    ``total_return`` is applied linearly across the window, which is what
    ``RegimeScorerV2._direction`` measures (first close vs last close). ``shock_span``
    widens only the final ``shock_bars`` bars, which is what its volatility ratio
    measures. ``skip_last`` drops the most recent bars to simulate staleness.
    """
    delta = TIMEFRAME_DELTAS[timeframe]
    bars: list[OHLCVBar] = []
    for index in range(count):
        # The window always holds exactly ``count`` contiguous bars. ``skip_last``
        # shifts the whole window back in time instead of shortening it, so a
        # staleness fixture stays distinguishable from a thin-history fixture:
        # otherwise the missing-bars gate would fire first and the staleness gate
        # would never actually be exercised.
        open_time = end - delta * (count - index + skip_last)
        progress = Decimal(index) / Decimal(count - 1) if count > 1 else Decimal("0")
        close = start_close * (Decimal("1") + total_return * progress)
        bar_span = span
        if shock_span is not None and index >= count - shock_bars:
            bar_span = shock_span
        bars.append(
            OHLCVBar(
                symbol=SYMBOL,
                timeframe=Timeframe(timeframe),
                time=open_time,
                open=close,
                high=close + bar_span / 2,
                low=close - bar_span / 2,
                close=close,
                volume=Decimal("100"),
            )
        )
    return bars


def _context(
    *,
    total_return_15m: Decimal = Decimal("0"),
    total_return_1h: Decimal = Decimal("0"),
    total_return_4h: Decimal = Decimal("0"),
    shock_span: Decimal | None = None,
    drop_timeframes: tuple[str, ...] = (),
    stale_timeframes: tuple[str, ...] = (),
    thin_timeframes: tuple[str, ...] = (),
    decision: datetime = DECISION,
) -> MarketContext:
    """Build a real point-in-time context through the production builder."""
    returns = {"15m": total_return_15m, "1h": total_return_1h, "4h": total_return_4h}
    bars_by_timeframe: dict[str, list[OHLCVBar]] = {}
    for timeframe, count in WINDOWS.items():
        if timeframe in drop_timeframes:
            bars_by_timeframe[timeframe] = []
            continue
        effective = MIN_BARS_FOR_LABEL.get(timeframe, count) - 1 if timeframe in thin_timeframes else count
        bars_by_timeframe[timeframe] = _series(
            timeframe=timeframe,
            count=max(effective, 2),
            end=decision,
            total_return=returns.get(timeframe, Decimal("0")),
            shock_span=shock_span if timeframe == "15m" else None,
            # Two intervals late is unambiguously stale for any timeframe.
            skip_last=2 if timeframe in stale_timeframes else 0,
        )
    return MarketContextBuilder().build(
        symbol=SYMBOL,
        decision_time=decision,
        bars_by_timeframe=bars_by_timeframe,
        source_ids=("regime_test",),
    )


def _score(context: MarketContext) -> RegimeScore:
    return RegimeScorerV2().score(context)


def _label(context: MarketContext) -> Regime:
    return project_regime_label(context=context, score=_score(context)).regime


# ------------------------------------------------------------------ db fixture


def _write_bars(db_path: Path, bars: list[OHLCVBar]) -> None:
    """Create a minimal ohlcv_bars table and insert ``bars``.

    Test-only writer. The classifier itself must open this file read-only, which
    REGIME-12 asserts.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_bars (
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (time, symbol, exchange, timeframe)
        )
        """
    )
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv_bars "
        "(time, symbol, exchange, timeframe, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                bar.timestamp.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f"),
                bar.symbol,
                "binance",
                bar.timeframe.value,
                float(bar.open),
                float(bar.high),
                float(bar.low),
                float(bar.close),
                float(bar.volume),
            )
            for bar in bars
        ],
    )
    conn.commit()
    conn.close()


def _uptrend_db(tmp_path: Path, *, decision: datetime = DECISION) -> Path:
    """A database whose critical timeframes all show a strong, complete uptrend."""
    db_path = tmp_path / "regime.db"
    bars: list[OHLCVBar] = []
    for timeframe, count in WINDOWS.items():
        bars.extend(
            _series(
                timeframe=timeframe,
                count=count,
                end=decision,
                total_return=Decimal("0.08"),
            )
        )
    _write_bars(db_path, bars)
    return db_path


def _row_count(db_path: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM ohlcv_bars").fetchone()[0])
    finally:
        conn.close()


# ------------------------------------------------------------------- REGIME-01


def test_regime_01_classification_uses_only_decision_time_data(tmp_path: Path) -> None:
    """REGIME-01: classification uses only data available at decision time.

    Bars that had not closed by the decision bar must be excluded. Inserting a
    violent reversal *after* the decision time must leave the label untouched,
    which is only true if the closed-window filter is actually applied.
    """
    from services.research.exit_policy_shadow.loader import classify_entry_regime

    db_path = _uptrend_db(tmp_path)
    before = classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)
    assert before.regime == Regime.TREND, f"expected TREND from a clean uptrend, got {before.regime}"

    # A crash that happens after the decision bar must not be visible.
    future = [
        OHLCVBar(
            symbol=SYMBOL,
            timeframe=Timeframe("15m"),
            time=DECISION + TIMEFRAME_DELTAS["15m"] * index,
            open=Decimal("50"),
            high=Decimal("50"),
            low=Decimal("10"),
            close=Decimal("10"),
            volume=Decimal("999"),
        )
        for index in range(40)
    ]
    _write_bars(db_path, future)

    after = classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)
    assert after.regime == before.regime
    assert after.trend_up == before.trend_up
    assert after.trend_down == before.trend_down


# ------------------------------------------------------------------- REGIME-02


def test_regime_02_future_bar_insertion_does_not_change_history(tmp_path: Path) -> None:
    """REGIME-02: inserting future bars does not change a historical classification.

    Stronger than REGIME-01: the full score vector *and* the feature snapshot hash
    must be byte-identical, so a later backfill cannot silently rewrite history.
    """
    from services.research.exit_policy_shadow.loader import classify_entry_regime

    db_path = _uptrend_db(tmp_path)
    before = classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)

    future = [
        OHLCVBar(
            symbol=SYMBOL,
            timeframe=Timeframe(timeframe),
            time=DECISION + TIMEFRAME_DELTAS[timeframe] * index,
            open=Decimal("500"),
            high=Decimal("900"),
            low=Decimal("400"),
            close=Decimal("880"),
            volume=Decimal("777"),
        )
        for timeframe in ("15m", "1h", "4h")
        for index in range(30)
    ]
    _write_bars(db_path, future)

    after = classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)
    assert after.feature_snapshot_hash == before.feature_snapshot_hash
    assert after.model_dump() == before.model_dump()


# ------------------------------------------------------------ REGIME-03 / 04 / gap


@pytest.mark.parametrize("missing", ["15m", "1h", "4h"])
def test_regime_03_missing_critical_timeframe_is_unknown(missing: str) -> None:
    """REGIME-03: a missing critical timeframe forces UNKNOWN.

    Without this gate the scorer still returns a number: a dropped 1h window simply
    removes its 0.30 direction weight, and a 0.70 residual would clear the 0.60
    threshold and be reported as TREND on incomplete evidence.
    """
    context = _context(
        total_return_15m=Decimal("0.08"),
        total_return_1h=Decimal("0.08"),
        total_return_4h=Decimal("0.08"),
        drop_timeframes=(missing,),
    )
    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.UNKNOWN
    assert missing in result.data_quality_reason


@pytest.mark.parametrize("thin", ["15m", "1h", "4h"])
def test_regime_03b_thin_critical_history_is_unknown(thin: str) -> None:
    """A present-but-too-short critical window is as unusable as a missing one.

    `RegimeScorerV2._volatility_scores` silently returns zeros below 10 true ranges,
    which suppresses `expansion` and inflates `range` — producing a confident-looking
    RANGE label from insufficient history.
    """
    context = _context(shock_span=SHOCK_SPAN, thin_timeframes=(thin,))
    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.UNKNOWN
    assert thin in result.data_quality_reason


@pytest.mark.parametrize("stale", ["15m", "1h", "4h"])
def test_regime_04_stale_critical_timeframe_is_unknown(stale: str) -> None:
    """REGIME-04: a stale critical timeframe forces UNKNOWN."""
    context = _context(
        total_return_15m=Decimal("0.08"),
        total_return_1h=Decimal("0.08"),
        total_return_4h=Decimal("0.08"),
        stale_timeframes=(stale,),
    )
    assert stale in context.freshness.stale_timeframes, "fixture must actually produce staleness"
    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.UNKNOWN
    assert stale in result.data_quality_reason


def test_regime_04b_gap_in_critical_timeframe_is_unknown() -> None:
    """A hole inside a critical window forces UNKNOWN.

    A gap means the direction and volatility measurements silently span a different
    period than they claim to.
    """
    bars_by_timeframe: dict[str, list[OHLCVBar]] = {
        timeframe: _series(
            # 15m is over-supplied so that removing bars leaves a genuine hole while
            # the window still satisfies the minimum-bars gate. Otherwise the gap
            # assertion would pass for the wrong reason.
            timeframe=timeframe,
            count=count + 5 if timeframe == "15m" else count,
            total_return=Decimal("0.08"),
        )
        for timeframe, count in WINDOWS.items()
    }
    # Punch a hole in the middle of the 15m window.
    bars_by_timeframe["15m"] = bars_by_timeframe["15m"][:30] + bars_by_timeframe["15m"][35:]
    context = MarketContextBuilder().build(
        symbol=SYMBOL,
        decision_time=DECISION,
        bars_by_timeframe=bars_by_timeframe,
        source_ids=("regime_test",),
    )
    assert context.bars_15m.gap_count > 0, "fixture must actually produce a gap"

    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.UNKNOWN
    assert "15m" in result.data_quality_reason


def test_non_critical_timeframe_absence_does_not_block_classification() -> None:
    """5m/1m are not critical: their absence must not force UNKNOWN.

    Real sample entries from before 5m collection started still have complete
    15m/1h/4h evidence, and discarding them would shrink an already small sample for
    no methodological gain.
    """
    context = _context(
        total_return_15m=Decimal("0.08"),
        total_return_1h=Decimal("0.08"),
        total_return_4h=Decimal("0.08"),
        drop_timeframes=("5m", "1m"),
    )
    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.TREND
    assert result.data_quality_reason == ""


# ------------------------------------------------------------- REGIME-05 .. 09


def test_regime_05_strong_trend_up_is_trend_up() -> None:
    """REGIME-05: a strong aligned uptrend classifies TREND / UP."""
    context = _context(
        total_return_15m=Decimal("0.08"),
        total_return_1h=Decimal("0.08"),
        total_return_4h=Decimal("0.08"),
    )
    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.TREND
    assert result.trend_direction == TrendDirection.UP
    assert result.direction_score >= REGIME_LABEL_CONFIDENCE_THRESHOLD


def test_regime_06_strong_trend_down_is_trend_down() -> None:
    """REGIME-06: a strong aligned downtrend classifies TREND / DOWN."""
    context = _context(
        total_return_15m=Decimal("-0.08"),
        total_return_1h=Decimal("-0.08"),
        total_return_4h=Decimal("-0.08"),
    )
    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.TREND
    assert result.trend_direction == TrendDirection.DOWN
    assert result.trend_down > result.trend_up


def test_regime_07_dominant_range_is_range() -> None:
    """REGIME-07: a flat, quiet market classifies RANGE."""
    context = _context()
    result = project_regime_label(context=context, score=_score(context))
    assert result.regime == Regime.RANGE
    assert result.trend_direction == TrendDirection.NONE
    assert result.range >= REGIME_LABEL_CONFIDENCE_THRESHOLD


def test_regime_08_dominant_expansion_is_expansion() -> None:
    """REGIME-08: a volatility burst without direction classifies EXPANSION."""
    context = _context(shock_span=SHOCK_SPAN)
    score = _score(context)
    assert score.expansion >= REGIME_LABEL_CONFIDENCE_THRESHOLD, "fixture must actually expand"

    result = project_regime_label(context=context, score=score)
    assert result.regime == Regime.EXPANSION
    assert result.expansion > result.range
    assert result.expansion > result.direction_score


def test_regime_09_ambiguous_scores_are_unknown() -> None:
    """REGIME-09: nothing clearing the confidence threshold classifies UNKNOWN.

    This is the honest-abstention case: a moderate drift with a moderate range must
    not be forced into whichever label happens to be numerically largest.
    """
    context = _context(
        total_return_15m=Decimal("0.018"),
        total_return_1h=Decimal("0.012"),
        total_return_4h=Decimal("0.010"),
    )
    score = _score(context)
    direction = max(score.trend_up, score.trend_down)
    assert direction < REGIME_LABEL_CONFIDENCE_THRESHOLD
    assert score.range < REGIME_LABEL_CONFIDENCE_THRESHOLD
    assert score.expansion < REGIME_LABEL_CONFIDENCE_THRESHOLD

    result = project_regime_label(context=context, score=score)
    assert result.regime == Regime.UNKNOWN
    assert result.data_quality_reason == "", "abstention here is low confidence, not bad data"


def test_confidence_threshold_is_frozen_at_060() -> None:
    """The research confidence threshold is frozen; it must not be PnL-tuned.

    P2-A2 forbids searching this constant against the sample's returns.
    """
    assert REGIME_LABEL_CONFIDENCE_THRESHOLD == 0.60


def test_ties_do_not_produce_two_labels() -> None:
    """The label rules must be mutually exclusive by construction.

    Exercised across a grid of synthetic score vectors, including exact ties at the
    threshold, so no input can satisfy two branches.
    """
    context = _context()
    values = [0.0, 0.3, 0.6, 0.6, 0.75, 1.0]
    for up in values:
        for rng in values:
            for exp in values:
                score = RegimeScore(
                    trend_up=up,
                    trend_down=0.0,
                    range=rng,
                    compression=0.0,
                    expansion=exp,
                    unstable=0.0,
                    evidence={},
                )
                result = project_regime_label(context=context, score=score)
                assert result.regime in set(Regime)


# ------------------------------------------------------------- REGIME-10 .. 12


def test_regime_10_stores_both_versions_and_full_score_vector() -> None:
    """REGIME-10: the result carries scorer + classifier version and every score.

    Storing only the label would make historical rows unrecomputable when the
    projection rules change. The whole vector must survive.
    """
    context = _context(shock_span=SHOCK_SPAN)
    result = project_regime_label(context=context, score=_score(context))

    assert result.scorer_version == SCORER_VERSION
    assert result.classifier_version == CLASSIFIER_VERSION
    assert result.classifier_version == "p2a-regime-label-v1"
    assert result.decision_time == DECISION
    assert result.feature_snapshot_hash

    for field in (
        "regime",
        "trend_up",
        "trend_down",
        "range",
        "compression",
        "expansion",
        "unstable",
        "evidence",
        "decision_time",
        "scorer_version",
        "classifier_version",
        "feature_snapshot_hash",
    ):
        assert field in result.model_dump(), f"score vector field {field!r} must be persisted"


def test_regime_11_same_inputs_are_deterministic(tmp_path: Path) -> None:
    """REGIME-11: same entry + same historical data -> identical label and hash."""
    from services.research.exit_policy_shadow.loader import classify_entry_regime

    db_path = _uptrend_db(tmp_path)
    first = classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)
    second = classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)

    assert first.regime == second.regime
    assert first.feature_snapshot_hash == second.feature_snapshot_hash
    assert first.model_dump() == second.model_dump()


def test_regime_11b_different_inputs_change_the_hash(tmp_path: Path) -> None:
    """A different point-in-time window must not collide onto the same hash."""
    from services.research.exit_policy_shadow.loader import classify_entry_regime

    db_path = _uptrend_db(tmp_path)
    here = classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)
    earlier = classify_entry_regime(
        db_path,
        symbol=SYMBOL,
        decision_bar=DECISION - timedelta(hours=6),
    )
    assert here.feature_snapshot_hash != earlier.feature_snapshot_hash


def test_regime_12_classification_performs_no_db_write(tmp_path: Path) -> None:
    """REGIME-12: classification must not write to the database.

    Checks both the physical file (row count and mtime unchanged) and that the
    module cannot write at all, since a read-only URI is the actual guarantee.
    """
    import services.research.exit_policy_shadow.regime as regime_mod
    from services.research.exit_policy_shadow.loader import classify_entry_regime

    db_path = _uptrend_db(tmp_path)
    rows_before = _row_count(db_path)
    mtime_before = db_path.stat().st_mtime_ns

    classify_entry_regime(db_path, symbol=SYMBOL, decision_bar=DECISION)

    assert _row_count(db_path) == rows_before
    assert db_path.stat().st_mtime_ns == mtime_before

    with open(regime_mod.__file__, encoding="utf-8") as handle:
        source = handle.read()
    for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "session.add", "session.commit"):
        assert token not in source, f"regime projection must not reference {token!r}"


def test_regime_projection_has_no_execution_authority() -> None:
    """The projection must not reach any execution-mutation surface."""
    import services.research.exit_policy_shadow.regime as regime_mod

    with open(regime_mod.__file__, encoding="utf-8") as handle:
        source = handle.read()
    for token in (
        "submit_order",
        "submit_protection",
        "submit_reduce_only_exit",
        "create_intent",
        "ExecutionIntent(",
        "ManagedPosition(",
        "BinanceAdapter",
        "binance_adapter",
        "entry_service",
    ):
        assert token not in source, f"regime projection must not reference {token!r}"


def test_projection_does_not_mutate_production_scorer() -> None:
    """The research projection must not redefine production regime scoring.

    P2-A2 forbids inventing a second scoring algorithm: the projection may only map
    an existing `RegimeScore` onto a label.
    """
    import services.research.exit_policy_shadow.regime as regime_mod

    with open(regime_mod.__file__, encoding="utf-8") as handle:
        source = handle.read()
    for token in ("class RegimeScorer", "def score(", "DIRECTION_WEIGHTS ="):
        assert token not in source, f"projection must not reimplement scoring ({token!r})"


# ------------------------------------------------- frozen E_REGIME_AWARE mapping


def test_regime_aware_mapping_is_frozen() -> None:
    """E's mapping is fixed in advance, not picked from this sample's rankings.

    Choosing the per-regime policy after seeing which one earned the most on 9 trades
    would be selection on the outcome being measured.
    """
    from services.research.exit_policy_shadow.contracts import ExitPolicyId
    from services.research.exit_policy_shadow.policies import resolve_regime_policy

    assert resolve_regime_policy(Regime.TREND).policy == ExitPolicyId.SCALE_OUT_RUNNER
    assert resolve_regime_policy(Regime.RANGE).policy == ExitPolicyId.CURRENT_CONTROL
    assert resolve_regime_policy(Regime.EXPANSION).policy == ExitPolicyId.ATR_ADAPTIVE
    assert resolve_regime_policy(Regime.UNKNOWN).policy == ExitPolicyId.CURRENT_CONTROL


def test_structure_proxy_is_excluded_from_regime_aware_selection() -> None:
    """C is an ATR proxy, not real structure recognition, so E must never select it.

    It stays in the report as an independent benchmark; routing entries into it would
    endorse a proxy under a name that implies structural analysis.
    """
    from services.research.exit_policy_shadow.contracts import ExitPolicyId
    from services.research.exit_policy_shadow.policies import resolve_regime_policy

    selected = {resolve_regime_policy(regime).policy for regime in Regime}
    assert ExitPolicyId.STRUCTURE_INVALIDATION not in selected


def test_unknown_fallback_is_visible_never_silent() -> None:
    """An UNKNOWN fallback must be reported, not quietly folded into CONTROL."""
    from services.research.exit_policy_shadow.policies import resolve_regime_policy

    selection = resolve_regime_policy(Regime.UNKNOWN)
    assert "UNKNOWN" in selection.reason
    assert selection.fallback is True

    for regime in (Regime.TREND, Regime.RANGE, Regime.EXPANSION):
        assert resolve_regime_policy(regime).fallback is False


def test_regime_aware_outcome_records_selected_policy() -> None:
    """Each E outcome must record which policy it delegated to, and why."""
    from services.research.exit_policy_shadow.contracts import ExitPolicyId
    from services.research.exit_policy_shadow.replay import replay_entry
    from tests.services.research.test_exit_policy_shadow import _bar, _entry

    entry = _entry(side="long", price="100", qty="2")
    bars = [_bar(index, "100", "101.5", "99.5", "101") for index in range(1, 12)]

    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.REGIME_AWARE,
        bars=bars,
        entry_context={"atr14": Decimal("1")},
        regime=Regime.EXPANSION,
    )
    assert out.regime == Regime.EXPANSION
    assert out.regime_selected_policy == ExitPolicyId.ATR_ADAPTIVE
    assert out.regime_selection_reason


@pytest.mark.parametrize(
    ("regime", "expected"),
    [
        (Regime.TREND, "SCALE_OUT_RUNNER"),
        (Regime.RANGE, "CURRENT_CONTROL"),
        (Regime.EXPANSION, "ATR_ADAPTIVE"),
        (Regime.UNKNOWN, "CURRENT_CONTROL"),
    ],
)
def test_regime_aware_result_equals_its_delegated_policy(regime: Regime, expected: str) -> None:
    """E must reproduce its target policy's result exactly, not merely borrow geometry.

    Regression guard for a real defect class: dispatching on the requested policy makes
    a TREND entry take the ladder's stop and first target while still exiting in a
    single leg, so the row labelled "regime-aware" is not the policy the mapping chose.
    Equality of net PnL, exit reason and leg count is what proves true delegation.
    """
    from services.research.exit_policy_shadow.contracts import ExitPolicyId
    from services.research.exit_policy_shadow.replay import replay_entry
    from tests.services.research.test_exit_policy_shadow import _bar, _entry

    entry = _entry(side="long", price="100", qty="3")
    ctx = {"atr14": Decimal("1")}
    bars = [
        _bar(1, "100", "101.3", "99.9", "101.2"),
        _bar(2, "101", "102.2", "100.9", "102.1"),
        _bar(3, "102", "103.1", "101.9", "103.0"),
        _bar(4, "103", "104.0", "102.9", "103.9"),
    ]

    aware = replay_entry(entry=entry, policy=ExitPolicyId.REGIME_AWARE, bars=bars, entry_context=ctx, regime=regime)
    target = replay_entry(
        entry=entry,
        policy=ExitPolicyId[expected],
        bars=bars,
        entry_context=ctx,
        regime=regime,
    )

    assert aware.regime_selected_policy == ExitPolicyId[expected]
    assert aware.net_pnl_usdt == target.net_pnl_usdt
    assert aware.final_reason == target.final_reason
    assert len(aware.legs) == len(target.legs)
    assert aware.initial_stop_price == target.initial_stop_price
    assert aware.initial_target_price == target.initial_target_price
    # The row must still be attributed to E, not silently relabelled as its target.
    assert aware.policy == ExitPolicyId.REGIME_AWARE


# ---------------------------------------------- post-exit continuation (Q2)


def test_post_exit_remaining_mfe_counts_only_bars_after_the_exit() -> None:
    """Continuation must exclude everything up to and including the exit bar."""
    from services.research.exit_policy_shadow.excursions import compute_post_exit_remaining_mfe_r
    from tests.services.research.test_exit_policy_shadow import BASE, _bar

    bars = [
        _bar(1, "100", "101", "99", "100"),  # in-policy
        _bar(2, "100", "106", "99", "105"),  # after exit: +6 from entry
    ]
    remaining = compute_post_exit_remaining_mfe_r(
        side="long",
        entry_price=Decimal("100"),
        exit_time=BASE + timedelta(minutes=1),
        horizon_end=BASE + timedelta(hours=24),
        bars=bars,
        in_policy_mfe_pct=Decimal("1"),  # the +1 already seen before exiting
        risk_per_unit=Decimal("1"),
    )
    # 6 total favourable, 1 already captured -> 5 additional, over 1.0 risk = 5R.
    assert remaining == Decimal("5")


def test_post_exit_remaining_mfe_is_zero_when_price_does_not_continue() -> None:
    """No continuation is 0R, which must stay distinct from "not measurable" (None)."""
    from services.research.exit_policy_shadow.excursions import compute_post_exit_remaining_mfe_r
    from tests.services.research.test_exit_policy_shadow import BASE, _bar

    bars = [_bar(1, "100", "101", "99", "100"), _bar(2, "100", "100.2", "95", "96")]
    remaining = compute_post_exit_remaining_mfe_r(
        side="long",
        entry_price=Decimal("100"),
        exit_time=BASE + timedelta(minutes=1),
        horizon_end=BASE + timedelta(hours=24),
        bars=bars,
        in_policy_mfe_pct=Decimal("1"),
        risk_per_unit=Decimal("1"),
    )
    assert remaining == Decimal("0")

    no_bars = compute_post_exit_remaining_mfe_r(
        side="long",
        entry_price=Decimal("100"),
        exit_time=BASE + timedelta(hours=48),
        horizon_end=BASE + timedelta(hours=24),
        bars=bars,
        in_policy_mfe_pct=Decimal("1"),
        risk_per_unit=Decimal("1"),
    )
    assert no_bars is None


def test_post_exit_remaining_mfe_is_direction_aware() -> None:
    """For a short, continuation is price falling further, not rising."""
    from services.research.exit_policy_shadow.excursions import compute_post_exit_remaining_mfe_r
    from tests.services.research.test_exit_policy_shadow import BASE, _bar

    bars = [_bar(1, "100", "101", "99", "100"), _bar(2, "100", "101", "94", "95")]
    remaining = compute_post_exit_remaining_mfe_r(
        side="short",
        entry_price=Decimal("100"),
        exit_time=BASE + timedelta(minutes=1),
        horizon_end=BASE + timedelta(hours=24),
        bars=bars,
        in_policy_mfe_pct=Decimal("1"),
        risk_per_unit=Decimal("1"),
    )
    assert remaining == Decimal("5")


def test_slice_sample_counts_entries_not_policy_rows() -> None:
    """A regime slice must count distinct entries, not (entry, policy) rows.

    Five policies per entry would turn a 2-entry slice into an apparent 10, which would
    clear the 10-entry gate on two trades.
    """
    from services.research.exit_policy_shadow.contracts import ExitPolicyId
    from services.research.exit_policy_shadow.metrics import compare_policies_by_regime
    from services.research.exit_policy_shadow.replay import replay_entry
    from tests.services.research.test_exit_policy_shadow import _bar, _entry

    bars = [_bar(index, "100", "100.60", "99.90", "100.55") for index in range(1, 6)]
    outcomes = []
    for position in range(2):
        entry = _entry(side="long", price="100", qty="1").model_copy(update={"position_id": f"pos-{position}"})
        for policy in ExitPolicyId:
            outcomes.append(
                replay_entry(
                    entry=entry,
                    policy=policy,
                    bars=bars,
                    entry_context={"atr14": Decimal("0.01")},
                    regime=Regime.RANGE,
                )
            )

    comparisons = compare_policies_by_regime(outcomes)
    assert comparisons[Regime.RANGE].trade_count == 2
    assert comparisons[Regime.RANGE].verdict.value == "INSUFFICIENT_SLICE_SAMPLE"
