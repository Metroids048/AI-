from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from services.strategy_library.context import MarketContext, MarketContextBuilder
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule
from services.validation.proposal_replay import ProposalReplayRunner, ReplayCostModel
from shared.models import Exchange, OHLCVBar, Timeframe


def _bar(opened_at: datetime, *, open_: str, high: str, low: str, close: str) -> OHLCVBar:
    return OHLCVBar(
        symbol="BTC/USDT",
        exchange=Exchange.BINANCE,
        timeframe=Timeframe.M15,
        time=opened_at,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
    )


def _context(decision_time: datetime) -> MarketContext:
    bars = [
        _bar(decision_time - timedelta(minutes=15 * item), open_="100", high="102", low="98", close="100")
        for item in range(80, 0, -1)
    ]
    return MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=decision_time,
        bars_by_timeframe=dict.fromkeys(("1m", "5m", "15m", "1h", "4h"), bars),
    )


def _proposal(context: MarketContext) -> StrategyProposal:
    time = context.bars_15m.last_closed_at
    assert time is not None
    return StrategyProposal(
        proposal_id="p1",
        strategy_id="research",
        strategy_version="1",
        symbol="BTC/USDT",
        side="long",
        setup_type="test",
        signal_bar_time=time,
        expires_at=time + timedelta(minutes=30),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation",
            reference_price=Decimal("100"),
            max_price_drift_bps=Decimal("20"),
        ),
        invalidation=InvalidationRule(stop_price=Decimal("95"), extreme_price=Decimal("95"), reason="test"),
        targets=(TargetRule(label="target", price=Decimal("105"), quantity_fraction=Decimal("1")),),
        regime_fit=0.8,
        setup_quality=0.8,
        cost_adjusted_rr=Decimal("1.5"),
        confidence_components={},
        feature_snapshot_hash="a" * 64,
        reasons=("test",),
    )


def test_replay_enters_on_next_bar_open_and_applies_costs() -> None:
    decision_time = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    context = _context(decision_time)
    next_bar = _bar(decision_time, open_="100", high="106", low="99", close="105")
    runner = ProposalReplayRunner(
        cost_model=ReplayCostModel(partial_fill_fraction=Decimal("1"), funding_rate_available=True)
    )

    metrics = runner.replay(strategy_id="research", contexts=[context], next_entry_bars=[next_bar], generator=_proposal)

    assert metrics.total_proposals == 1
    assert metrics.total_trades == 1
    assert metrics.trades[0].opened_at == next_bar.timestamp
    assert metrics.trades[0].entry_price > next_bar.open
    assert metrics.trades[0].exit_price == Decimal("105.01899200")
    assert metrics.trades[0].net_return < metrics.trades[0].gross_return


def test_replay_rejects_next_bar_when_price_drift_exceeds_proposal_limit() -> None:
    decision_time = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    context = _context(decision_time)
    next_bar = _bar(decision_time, open_="101", high="102", low="100", close="101")

    metrics = ProposalReplayRunner().replay(
        strategy_id="research", contexts=[context], next_entry_bars=[next_bar], generator=_proposal
    )

    assert metrics.total_trades == 0
    assert metrics.rejected_price_drift == 1


def test_replay_only_enters_selector_selected_candidate() -> None:
    decision_time = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    context = _context(decision_time)
    selected = _proposal(context)

    def pipeline(_context: MarketContext) -> SimpleNamespace:
        return SimpleNamespace(selection=SimpleNamespace(selected=selected))

    rejected = ProposalReplayRunner().replay(
        strategy_id="other_candidate",
        contexts=[context],
        next_entry_bars=[_bar(decision_time, open_="100", high="106", low="99", close="105")],
        pipeline=pipeline,
    )
    accepted = ProposalReplayRunner().replay(
        strategy_id="proposal_pipeline",
        contexts=[context],
        next_entry_bars=[_bar(decision_time, open_="100", high="106", low="99", close="105")],
        pipeline=pipeline,
    )

    assert rejected.total_proposals == 0
    assert accepted.total_proposals == 1


def test_replay_uses_stop_first_when_a_bar_hits_stop_and_target() -> None:
    decision_time = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    contexts = [_context(decision_time), _context(decision_time + timedelta(minutes=15))]
    bars = [
        _bar(decision_time, open_="100", high="101", low="99", close="100"),
        _bar(decision_time + timedelta(minutes=15), open_="100", high="106", low="94", close="100"),
    ]

    def first_proposal_only(context: MarketContext) -> StrategyProposal | None:
        return _proposal(context) if context.decision_time == decision_time else None

    metrics = ProposalReplayRunner(cost_model=ReplayCostModel(partial_fill_fraction=Decimal("1"))).replay(
        strategy_id="research", contexts=contexts, next_entry_bars=bars, generator=first_proposal_only
    )

    assert metrics.total_trades == 1
    assert metrics.trades[0].exit_reason == "stop_loss"


def test_funding_cost_respects_side_and_rate_sign() -> None:
    decision_time = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    contexts = [_context(decision_time), _context(decision_time + timedelta(hours=8))]
    bars = [
        _bar(decision_time, open_="100", high="101", low="99", close="100"),
        _bar(decision_time + timedelta(hours=8), open_="100", high="101", low="99", close="100"),
    ]

    def proposal_for(side: str):  # noqa: ANN202
        def generate(context: MarketContext) -> StrategyProposal | None:
            if context.decision_time != decision_time:
                return None
            proposal = _proposal(context)
            if side == "long":
                return proposal
            return proposal.model_copy(
                update={
                    "side": "short",
                    "invalidation": InvalidationRule(
                        stop_price=Decimal("105"), extreme_price=Decimal("105"), reason="test"
                    ),
                    "targets": (TargetRule(label="target", price=Decimal("95"), quantity_fraction=Decimal("1")),),
                }
            )

        return generate

    cost = ReplayCostModel(
        partial_fill_fraction=Decimal("1"),
        funding_rate_per_interval=Decimal("0.001"),
        funding_rate_available=True,
    )
    long_metrics = ProposalReplayRunner(cost_model=cost).replay(
        strategy_id="research", contexts=contexts, next_entry_bars=bars, generator=proposal_for("long")
    )
    short_metrics = ProposalReplayRunner(cost_model=cost).replay(
        strategy_id="research", contexts=contexts, next_entry_bars=bars, generator=proposal_for("short")
    )

    assert long_metrics.trades[0].funding_cost == Decimal("0.001")
    assert short_metrics.trades[0].funding_cost == Decimal("-0.001")
    assert short_metrics.trades[0].net_return > long_metrics.trades[0].net_return


def test_missing_funding_remains_unavailable_and_blocks_promotion_evidence() -> None:
    decision_time = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    metrics = ProposalReplayRunner().replay(
        strategy_id="research",
        contexts=[_context(decision_time)],
        next_entry_bars=[_bar(decision_time, open_="100", high="106", low="99", close="105")],
        generator=_proposal,
    )

    assert metrics.funding_rate_available is False
    assert metrics.cost_provenance["funding_rate_per_interval"] == "UNAVAILABLE"
    assert metrics.promotion_observations_complete is False
    assert metrics.trades[0].funding_cost is None


def test_funding_cost_sums_observed_settlement_events_during_holding_period() -> None:
    decision_time = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    contexts = [_context(decision_time), _context(decision_time + timedelta(hours=8))]
    bars = [
        _bar(decision_time, open_="100", high="101", low="99", close="100"),
        _bar(decision_time + timedelta(hours=8), open_="100", high="101", low="99", close="100"),
    ]
    cost = ReplayCostModel(
        partial_fill_fraction=Decimal("1"),
        funding_points=(
            (decision_time - timedelta(hours=1), Decimal("0.009")),
            (decision_time + timedelta(hours=4), Decimal("0.001")),
            (decision_time + timedelta(hours=8), Decimal("-0.002")),
        ),
    )

    metrics = ProposalReplayRunner(cost_model=cost).replay(
        strategy_id="research",
        contexts=contexts,
        next_entry_bars=bars,
        generator=lambda context: _proposal(context) if context.decision_time == decision_time else None,
    )

    assert metrics.trades[0].funding_cost == Decimal("-0.001")


def test_assumed_execution_costs_block_promotion_even_with_observed_funding() -> None:
    cost = ReplayCostModel(
        funding_points=((datetime(2025, 1, 1, tzinfo=UTC), Decimal("0.001")),),
    )

    assert cost.funding_provenance == "OBSERVED"
    assert cost.spread_provenance == "ASSUMED"
    assert cost.latency_provenance == "ASSUMED"
    assert cost.partial_fill_provenance == "ASSUMED"
    assert cost.promotion_observations_complete is False
