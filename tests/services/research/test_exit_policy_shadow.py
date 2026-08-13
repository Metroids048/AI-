"""P2-A mandated test suite. RED before GREEN.

Covers the 13 tests named in the P2-A task contract. Synthetic bars are used here
deliberately: unit tests must be deterministic. Synthetic data never reaches the
real-sample business report.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from services.research.exit_policy_shadow.contracts import (
    Bar,
    ExcursionMetrics,
    ExitPolicyId,
    ExitReason,
    IntrabarResolution,
    RealEntry,
    Regime,
    Verdict,
)
from services.research.exit_policy_shadow.excursions import compute_excursions
from services.research.exit_policy_shadow.policies import (
    build_initial_geometry,
    resolve_regime_policy,
)
from services.research.exit_policy_shadow.replay import replay_entry

BASE = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)


def _bar(offset_min: int, o: str, h: str, low: str, c: str, v: str = "100") -> Bar:
    return Bar(
        time=BASE + timedelta(minutes=offset_min),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
    )


def _entry(side: str = "long", price: str = "100", qty: str = "1") -> RealEntry:
    return RealEntry(
        position_id=f"pos-{side}",
        symbol="BTC/USDT",
        side=side,  # type: ignore[arg-type]
        average_fill_price=Decimal(price),
        fill_timestamp=BASE,
        filled_quantity=Decimal(qty),
        entry_fee_usdt=Decimal("0.05"),
        candidate_key="testnet_sampling_v2",
        decision_bar_timestamp=BASE,
        exchange_order_id="123456",
    )


def _flat_context(atr: str = "1.0") -> dict[str, Decimal]:
    return {"atr14": Decimal(atr)}


# ---------------------------------------------------------------- immutability


def test_real_entry_is_immutable() -> None:
    """TEST_REAL_ENTRY_IS_IMMUTABLE: entry facts cannot be mutated."""
    entry = _entry()
    with pytest.raises(ValidationError):
        entry.average_fill_price = Decimal("999")  # type: ignore[misc]
    with pytest.raises(ValidationError):
        entry.filled_quantity = Decimal("999")  # type: ignore[misc]

    # Replaying must not alter the entry object either.
    bars = [_bar(i, "100", "101", "99", "100") for i in range(1, 20)]
    before = entry.model_dump()
    replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=_flat_context(),
        regime=Regime.RANGE,
    )
    assert entry.model_dump() == before


def test_real_entry_rejects_non_sampling_provenance() -> None:
    """A research proposal must never be accepted as a real CONTROL fill."""
    with pytest.raises(ValueError, match="testnet_sampling_v2"):
        RealEntry(
            position_id="p",
            symbol="BTC/USDT",
            side="long",
            average_fill_price=Decimal("100"),
            fill_timestamp=BASE,
            filled_quantity=Decimal("1"),
            entry_fee_usdt=Decimal("0"),
            candidate_key="trend_pullback_v2",
            decision_bar_timestamp=BASE,
            exchange_order_id="1",
        )


# ------------------------------------------------------------ point-in-time


def test_no_future_data_in_initial_geometry() -> None:
    """TEST_NO_FUTURE_DATA_IN_INITIAL_GEOMETRY.

    Initial stop/target must depend only on entry-time context. Two replays whose
    post-entry bars differ wildly must produce identical initial geometry.
    """
    entry = _entry()
    ctx = _flat_context()

    calm = [_bar(i, "100", "100.5", "99.5", "100") for i in range(1, 40)]
    violent = [_bar(i, "100", "140", "60", "100") for i in range(1, 40)]

    a = replay_entry(
        entry=entry,
        policy=ExitPolicyId.STRUCTURE_INVALIDATION,
        bars=calm,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    b = replay_entry(
        entry=entry,
        policy=ExitPolicyId.STRUCTURE_INVALIDATION,
        bars=violent,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert a.initial_stop_price == b.initial_stop_price
    assert a.initial_target_price == b.initial_target_price


def test_control_policy_reproduces_production_formula() -> None:
    """Policy A must reproduce max(1.2*ATR14, price*0.0035) and 1.5R exactly."""
    # ATR term dominates.
    stop, target = build_initial_geometry(
        policy=ExitPolicyId.CURRENT_CONTROL,
        side="long",
        entry_price=Decimal("100"),
        entry_context={"atr14": Decimal("10")},
        regime=Regime.TREND,
    )
    assert Decimal("100") - stop == Decimal("12")  # 1.2 * 10
    assert target is not None
    assert target - Decimal("100") == Decimal("18")  # 1.5 * 12

    # Percentage floor dominates.
    stop, target = build_initial_geometry(
        policy=ExitPolicyId.CURRENT_CONTROL,
        side="long",
        entry_price=Decimal("100"),
        entry_context={"atr14": Decimal("0.01")},
        regime=Regime.TREND,
    )
    assert Decimal("100") - stop == Decimal("0.35")
    assert target is not None
    assert target - Decimal("100") == Decimal("0.525")


# ------------------------------------------------------------------ MFE / MAE


def test_long_mfe_mae() -> None:
    """TEST_LONG_MFE_MAE: favourable is up, adverse is down."""
    entry_price = Decimal("100")
    bars = [
        _bar(1, "100", "104", "98", "100"),  # +4% / -2%
        _bar(2, "100", "102", "97", "100"),  # -3% is the new worst
    ]
    exc = compute_excursions(
        side="long",
        entry_price=entry_price,
        quantity=Decimal("2"),
        bars=bars,
        risk_per_unit=Decimal("2"),
    )
    assert exc.mfe_pct == Decimal("4")
    assert exc.mae_pct == Decimal("-3")
    assert exc.mfe_pnl_usdt == Decimal("8")  # 4 * 2 units
    assert exc.mae_pnl_usdt == Decimal("-6")  # -3 * 2 units
    assert exc.mfe_r == Decimal("2")  # 4 / 2
    assert exc.mae_r == Decimal("-1.5")  # -3 / 2


def test_short_mfe_mae() -> None:
    """TEST_SHORT_MFE_MAE: favourable is down, adverse is up. Sign-symmetric."""
    bars = [
        _bar(1, "100", "102", "96", "100"),  # favourable -4, adverse +2
        _bar(2, "100", "103", "98", "100"),  # adverse +3 is worse
    ]
    exc = compute_excursions(
        side="short",
        entry_price=Decimal("100"),
        quantity=Decimal("2"),
        bars=bars,
        risk_per_unit=Decimal("2"),
    )
    assert exc.mfe_pct == Decimal("4")
    assert exc.mae_pct == Decimal("-3")
    assert exc.mfe_pnl_usdt == Decimal("8")
    assert exc.mae_pnl_usdt == Decimal("-6")


def test_excursions_truncate_at_exit_bar() -> None:
    """Post-exit price action must not inflate MFE."""
    entry = _entry(side="long", price="100", qty="1")
    ctx = {"atr14": Decimal("0.01")}  # forces the 0.35% floor -> tight geometry
    bars = [
        _bar(1, "100", "100.60", "99.90", "100.55"),  # hits +0.525% target
        _bar(2, "100", "200", "100", "200"),  # huge move AFTER exit
    ]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.final_reason == ExitReason.TARGET
    # MFE must reflect only the exit bar, not the 100% move that followed.
    assert out.excursions.mfe_pct < Decimal("1")


# -------------------------------------------------- profit capture ratio


def test_profit_capture_ratio() -> None:
    """TEST_PROFIT_CAPTURE_RATIO: net / MFE in USDT, same unit both sides."""
    entry = _entry(side="short", price="1935", qty="1")
    ctx = {"atr14": Decimal("0.01")}
    # Price falls a long way (favourable for a short) but the tight target fires early.
    bars = [
        _bar(1, "1935", "1935.5", "1925", "1926"),
        _bar(2, "1926", "1930", "1868", "1870"),
    ]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    ratio = out.profit_capture_ratio
    assert ratio is not None
    assert Decimal("0") < ratio < Decimal("1")
    # It must equal net / mfe exactly, in USDT.
    assert ratio == out.net_pnl_usdt / out.excursions.mfe_pnl_usdt


def test_zero_mfe_capture_is_null() -> None:
    """TEST_ZERO_MFE_CAPTURE_IS_NULL: no favourable excursion -> None, never a ratio."""
    entry = _entry(side="long", price="100", qty="1")
    ctx = {"atr14": Decimal("0.01")}
    # Price only ever goes against a long; MFE is exactly 0.
    bars = [
        _bar(1, "100", "100", "99.60", "99.70"),
        _bar(2, "99.70", "99.80", "99.50", "99.60"),
    ]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.excursions.mfe_pnl_usdt <= 0
    assert out.profit_capture_ratio is None


# ---------------------------------------------------------------------- costs


def test_fees_included_in_net_pnl() -> None:
    """TEST_FEES_INCLUDED_IN_NET_PNL: costs are never silently zero."""
    entry = _entry(side="long", price="100", qty="1")
    ctx = {"atr14": Decimal("0.01")}
    bars = [_bar(1, "100", "100.60", "99.95", "100.55")]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.entry_fee_usdt > 0
    assert out.estimated_exit_fee_usdt > 0
    assert out.net_pnl_usdt < out.gross_pnl_usdt
    assert out.net_pnl_usdt == out.gross_pnl_usdt - out.total_cost_usdt


# ------------------------------------------------------- intrabar ambiguity


def test_intrabar_stop_first() -> None:
    """TEST_INTRABAR_STOP_FIRST: a bar touching both resolves conservatively."""
    entry = _entry(side="long", price="100", qty="1")
    ctx = {"atr14": Decimal("0.01")}  # stop 99.65, target 100.525
    bars = [_bar(1, "100", "101", "99", "100")]  # touches both
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.ambiguous_intrabar is True
    assert out.final_reason == ExitReason.STOP
    assert out.legs[-1].intrabar == IntrabarResolution.STOP_FIRST
    assert out.net_pnl_usdt < 0


def test_intrabar_target_first_sensitivity() -> None:
    """TEST_INTRABAR_TARGET_FIRST_SENSITIVITY: optimistic branch reported separately."""
    entry = _entry(side="long", price="100", qty="1")
    ctx = {"atr14": Decimal("0.01")}
    bars = [_bar(1, "100", "101", "99", "100")]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.sensitivity_net_pnl_usdt is not None
    # Sensitivity must be the strictly better branch, and must not overwrite primary.
    assert out.sensitivity_net_pnl_usdt > out.net_pnl_usdt


def test_unambiguous_bar_has_no_sensitivity() -> None:
    """A bar touching only one level must not be flagged ambiguous."""
    entry = _entry(side="long", price="100", qty="1")
    ctx = {"atr14": Decimal("0.01")}
    bars = [_bar(1, "100", "100.60", "99.90", "100.55")]  # target only
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.ambiguous_intrabar is False
    assert out.sensitivity_net_pnl_usdt is None


# ----------------------------------------------------------------- scale-out


def test_scale_out_quantity_sum() -> None:
    """TEST_SCALE_OUT_QUANTITY_SUM: legs + remaining must equal entry quantity."""
    entry = _entry(side="long", price="100", qty="3")
    ctx = {"atr14": Decimal("1")}  # stop 98.8, risk 1.2
    bars = [
        _bar(1, "100", "101.3", "99.9", "101.2"),  # >= 1R
        _bar(2, "101", "102.2", "100.9", "102.1"),  # >= 1.8R
        _bar(3, "102", "103.1", "101.9", "103.0"),  # >= 2.5R runner
    ]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.SCALE_OUT_RUNNER,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    legged = sum((leg.quantity for leg in out.legs), Decimal("0"))
    assert legged + out.remaining_quantity == entry.filled_quantity
    fractions = sum((leg.quantity_fraction for leg in out.legs), Decimal("0"))
    assert fractions <= Decimal("1")
    # Every leg must carry its own price, time and reason.
    for leg in out.legs:
        assert leg.price > 0
        assert leg.filled_at is not None
        assert leg.reason in {
            ExitReason.PARTIAL_TARGET,
            ExitReason.TARGET,
            ExitReason.STOP,
            ExitReason.RUNNER_TRAIL,
            ExitReason.DATA_EXHAUSTED,
        }


# -------------------------------------------------------------------- regime


def test_regime_uses_entry_time_state() -> None:
    """TEST_REGIME_USES_ENTRY_TIME_STATE: selection depends on entry-time regime only."""
    trend = resolve_regime_policy(Regime.TREND)
    rng = resolve_regime_policy(Regime.RANGE)
    assert trend.policy != rng.policy
    assert trend.reason
    assert rng.reason

    # The same bars under two different entry-time regimes must select differently.
    entry = _entry(side="long", price="100", qty="2")
    ctx = {"atr14": Decimal("1")}
    bars = [_bar(i, "100", "101.5", "99.5", "101") for i in range(1, 12)]
    a = replay_entry(
        entry=entry,
        policy=ExitPolicyId.REGIME_AWARE,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    b = replay_entry(
        entry=entry,
        policy=ExitPolicyId.REGIME_AWARE,
        bars=bars,
        entry_context=ctx,
        regime=Regime.RANGE,
    )
    assert a.regime_selection_reason is not None
    assert b.regime_selection_reason is not None
    assert a.regime_selection_reason != b.regime_selection_reason


def test_unknown_regime_falls_back_to_control() -> None:
    """An unclassifiable regime must fail closed onto the baseline, not guess."""
    selection = resolve_regime_policy(Regime.UNKNOWN)
    assert selection.policy == ExitPolicyId.CURRENT_CONTROL


# ------------------------------------------- research proposal reuse guard


def test_research_proposal_not_improperly_reused() -> None:
    """TEST_RESEARCH_PROPOSAL_NOT_IMPROPERLY_REUSED.

    P2-A policies must be generic and self-contained: none of them may import or
    depend on a research candidate's proposal generator. Full proposal comparison
    is P2-B.
    """
    import services.research.exit_policy_shadow.policies as policies_mod
    import services.research.exit_policy_shadow.replay as replay_mod

    source = ""
    for mod in (policies_mod, replay_mod):
        with open(mod.__file__, encoding="utf-8") as handle:
            source += handle.read()

    forbidden = [
        "trend_pullback_v2",
        "range_sweep_reversion_v1",
        "failed_breakout_reversal_v1",
        "evaluate_trend_pullback",
        "evaluate_range_sweep",
        "StrategyProposal",
        "proposal_pipeline",
    ]
    for token in forbidden:
        assert token not in source, (
            f"P2-A must not depend on research proposal machinery; found {token!r}. "
            "Complete proposal comparison belongs to P2-B."
        )


# --------------------------------------------------- no execution mutation


def test_no_execution_mutation() -> None:
    """TEST_NO_EXECUTION_MUTATION.

    The evaluator must not reference any execution-mutation surface: no intent
    creation, no order submission, no managed-position write, no protection
    submission, no Binance mutation adapter.
    """
    import services.research.exit_policy_shadow.excursions as exc_mod
    import services.research.exit_policy_shadow.metrics as metrics_mod
    import services.research.exit_policy_shadow.policies as policies_mod
    import services.research.exit_policy_shadow.replay as replay_mod

    forbidden = [
        "submit_order",
        "submit_entry",
        "submit_protection",
        "submit_reduce_only_exit",
        "create_intent",
        "ExecutionIntent(",
        "ManagedPosition(",
        "BinanceAdapter",
        "binance_adapter",
        "protection_service",
        "entry_service",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "session.add",
        "session.commit",
    ]
    for mod in (replay_mod, policies_mod, exc_mod, metrics_mod):
        with open(mod.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for token in forbidden:
            assert token not in source, f"{mod.__name__} must not reference {token!r}"


def test_capture_ratio_undefined_when_excursion_below_cost() -> None:
    """A sub-cost favourable excursion must not fabricate a huge negative ratio.

    Regression guard for a real observed case: MFE of 0.0002% with a small loss
    produced -107242%. `mfe > 0` alone is too weak a denominator guard.
    """
    entry = _entry(side="long", price="64996.20", qty="0.0388")
    ctx = {"atr14": Decimal("28.18")}
    bars = [
        # High is a hair above entry: arithmetically positive MFE, economically nil.
        _bar(1, "64996.20", "64996.30", "64953.00", "64960.00"),
    ]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.excursions.mfe_pnl_usdt > 0
    assert out.excursions.mfe_pnl_usdt <= out.total_cost_usdt
    assert out.profit_capture_ratio is None
    assert out.capture_ratio_undefined_reason == "excursion_below_round_trip_cost"


def test_capture_ratio_retains_genuine_negative() -> None:
    """A materially capturable profit that was given back stays negative, not None."""
    entry = _entry(side="long", price="100", qty="10")
    ctx = {"atr14": Decimal("0.01")}  # stop 99.65, target 100.525
    bars = [
        _bar(1, "100", "100.40", "99.90", "100.30"),  # MFE well above cost, no exit
        _bar(2, "100.30", "100.35", "99.00", "99.10"),  # stopped out
    ]
    out = replay_entry(
        entry=entry,
        policy=ExitPolicyId.CURRENT_CONTROL,
        bars=bars,
        entry_context=ctx,
        regime=Regime.TREND,
    )
    assert out.excursions.mfe_pnl_usdt > out.total_cost_usdt
    ratio = out.profit_capture_ratio
    assert ratio is not None and ratio < 0
    assert out.capture_ratio_undefined_reason is None


def test_q1_counts_each_entry_once() -> None:
    """Q1 must consume entry-level excursions, not per-(entry, policy) rows.

    Passing 5 policy rows per entry would quintuple the apparent sample and mix exit
    choice into a question about entry quality.
    """
    from services.research.exit_policy_shadow.metrics import answer_q1_entry_has_edge

    strong = ExcursionMetrics(
        mfe_pct=Decimal("2"),
        mae_pct=Decimal("-0.5"),
        mfe_r=Decimal("2"),
        mae_r=Decimal("-0.5"),
        mfe_pnl_usdt=Decimal("20"),
        mae_pnl_usdt=Decimal("-5"),
    )

    verdict, evidence = answer_q1_entry_has_edge([strong] * 10)
    assert verdict == Verdict.INSUFFICIENT_SAMPLE
    assert evidence.sample_count == 10
    assert "n=10" in evidence.describe()

    verdict, evidence = answer_q1_entry_has_edge([strong] * 30)
    assert verdict == Verdict.SUPPORTED
    assert evidence.sample_count == 30
    assert "n=30" in evidence.describe()
    # The observed numbers must survive regardless of the verdict, so a small sample
    # still reports real evidence instead of an empty string.
    assert evidence.mean_mfe_r == Decimal("2")
    assert evidence.positive_mfe_count == 30


def test_db_timestamp_format_matches_stored_convention() -> None:
    """Bar-window filters are string comparisons against space-separated timestamps.

    Regression guard: passing ``datetime.isoformat()`` into the range filter silently
    drops every bar on the start date, because ``'T'`` sorts after ``' '``. That made
    a replay begin hours late and still look plausible, so the format is pinned here.
    """
    from services.research.exit_policy_shadow.loader import _to_db_timestamp

    aware = datetime(2026, 8, 7, 10, 0, 39, 946000, tzinfo=UTC)
    rendered = _to_db_timestamp(aware)

    assert rendered == "2026-08-07 10:00:39.946000"
    assert "T" not in rendered
    assert "+" not in rendered

    # The whole point: a bar stored at 10:00 on the same date must not sort below
    # a fill at 10:00:39 in a way that discards the entire day.
    same_day_bar = "2026-08-07 09:59:00.000000"
    later_bar = "2026-08-07 10:01:00.000000"
    assert same_day_bar < rendered < later_bar

    # A naive datetime must render identically, not crash or shift.
    naive = datetime(2026, 8, 7, 10, 0, 39, 946000)
    assert _to_db_timestamp(naive) == rendered


def test_atr_requires_sufficient_history() -> None:
    """ATR must return None rather than a fabricated value on thin history."""
    from services.research.exit_policy_shadow.loader import compute_atr

    assert compute_atr([], period=14) is None
    assert compute_atr([_bar(i, "100", "101", "99", "100") for i in range(5)], period=14) is None

    enough = [_bar(i, "100", "102", "98", "100") for i in range(20)]
    atr = compute_atr(enough, period=14)
    assert atr is not None and atr > 0


def test_loader_opens_database_read_only() -> None:
    """The real-sample loader must physically open SQLite in read-only mode."""
    import services.research.exit_policy_shadow.loader as loader_mod

    with open(loader_mod.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "mode=ro" in source, "loader must use a read-only SQLite URI"
    for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP "):
        assert token not in source, f"loader must not contain {token!r}"
