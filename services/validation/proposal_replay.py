"""Deterministic, research-only replay for immutable strategy proposals.

The runner intentionally operates after a proposal has been generated.  It
therefore shares the proposal contract with runtime planning and keeps order
simulation, costs, and promotion evidence outside the execution system.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import zip_longest
from statistics import mean
from typing import Literal

from services.strategy_library.context import MarketContext
from services.strategy_library.exit.adaptive_exit import build_adaptive_exit
from services.strategy_library.proposals import StrategyProposal
from shared.models import OHLCVBar

ProposalGenerator = Callable[[MarketContext], StrategyProposal | None]


@dataclass(frozen=True)
class ReplayCostModel:
    """Costs applied symmetrically to a next-bar market fill.

    ``funding_rate_available`` is deliberately separate from the numeric value:
    a zero observed funding rate and an unavailable funding history are not the
    same research claim.
    """

    taker_fee_bps_per_side: Decimal = Decimal("5")
    spread_bps_per_side: Decimal = Decimal("1")
    latency_slippage_bps_per_side: Decimal = Decimal("1")
    partial_fill_fraction: Decimal = Decimal("0.85")
    funding_rate_per_interval: Decimal = Decimal("0")
    funding_interval_hours: Decimal = Decimal("8")
    funding_rate_available: bool = False

    def __post_init__(self) -> None:
        if self.taker_fee_bps_per_side < 0 or self.spread_bps_per_side < 0 or self.latency_slippage_bps_per_side < 0:
            raise ValueError("cost bps must be non-negative")
        if not Decimal("0") < self.partial_fill_fraction <= Decimal("1"):
            raise ValueError("partial_fill_fraction must be in (0, 1]")
        if self.funding_interval_hours <= 0:
            raise ValueError("funding_interval_hours must be positive")

    @property
    def entry_impact_bps(self) -> Decimal:
        return self.spread_bps_per_side + self.latency_slippage_bps_per_side

    @property
    def explicit_round_trip_cost_bps(self) -> Decimal:
        return Decimal("2") * (
            self.taker_fee_bps_per_side + self.spread_bps_per_side + self.latency_slippage_bps_per_side
        )


@dataclass(frozen=True)
class ProposalReplayTrade:
    proposal_id: str
    strategy_id: str
    symbol: str
    side: Literal["long", "short"]
    signal_time: datetime
    opened_at: datetime
    closed_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    exit_reason: str
    filled_fraction: Decimal
    gross_return: Decimal
    net_return: Decimal
    fees_and_impact_bps: Decimal
    funding_cost: Decimal
    target_labels_hit: tuple[str, ...]


@dataclass(frozen=True)
class ProposalReplayMetrics:
    strategy_id: str
    total_proposals: int
    expired_proposals: int
    rejected_price_drift: int
    total_trades: int
    win_rate: float
    average_profit_loss_ratio: float | None
    profit_factor: float
    net_expectancy: float
    net_return: float
    max_drawdown: float
    funding_rate_available: bool
    trades: tuple[ProposalReplayTrade, ...]


@dataclass
class _OpenProposalPosition:
    proposal: StrategyProposal
    opened_at: datetime
    entry_price: Decimal
    filled_fraction: Decimal
    initial_stop: Decimal
    targets: tuple[tuple[str, Decimal, Decimal], ...]
    remaining_fraction: Decimal
    realized_gross: Decimal = Decimal("0")
    realized_cost: Decimal = Decimal("0")
    target_labels_hit: list[str] = field(default_factory=list)
    last_exit_price: Decimal | None = None
    bars_held: int = 0


def _directional_return(*, side: str, entry: Decimal, exit_price: Decimal) -> Decimal:
    raw = (exit_price - entry) / entry
    return raw if side == "long" else -raw


def _drawdown(returns: Iterable[Decimal]) -> Decimal:
    equity = Decimal("1")
    peak = equity
    maximum = Decimal("0")
    for value in returns:
        equity += value
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


class ProposalReplayRunner:
    """Replay one candidate without touching execution or an active manifest."""

    def __init__(self, *, cost_model: ReplayCostModel | None = None) -> None:
        self.cost_model = cost_model or ReplayCostModel()

    def replay(
        self,
        *,
        strategy_id: str,
        contexts: Iterable[MarketContext],
        next_entry_bars: Iterable[OHLCVBar],
        generator: ProposalGenerator,
    ) -> ProposalReplayMetrics:
        """Replay contexts ordered by their closed 15m decision time.

        ``next_entry_bars`` must be the following entry-timeframe candle for
        each context.  Keeping that mapping explicit makes next-bar semantics
        testable and prevents a close-price fill from slipping back in.
        """

        position: _OpenProposalPosition | None = None
        trades: list[ProposalReplayTrade] = []
        total_proposals = 0
        expired = 0
        drift_rejected = 0

        last_bar: OHLCVBar | None = None
        missing = object()
        for context, entry_bar in zip_longest(contexts, next_entry_bars, fillvalue=missing):
            if context is missing or entry_bar is missing:
                raise ValueError("contexts and next_entry_bars must have the same length")
            assert isinstance(context, MarketContext)
            assert isinstance(entry_bar, OHLCVBar)
            last_bar = entry_bar
            if position is not None:
                closed = self._advance(position=position, bar=entry_bar)
                if closed is not None:
                    trades.append(closed)
                    position = None

            if position is None:
                proposal = generator(context)
                if proposal is not None:
                    total_proposals += 1
                    if entry_bar.timestamp >= proposal.expires_at:
                        expired += 1
                        continue
                    position = self._open(proposal=proposal, bar=entry_bar)
                    if position is None:
                        drift_rejected += 1
                    else:
                        # A market order fills at this new bar's open, so the rest
                        # of that bar may legitimately hit protection or targets.
                        closed = self._advance(position=position, bar=entry_bar)
                        if closed is not None:
                            trades.append(closed)
                            position = None

        if position is not None and last_bar is not None:
            trades.append(
                self._close(
                    position=position,
                    closed_at=last_bar.timestamp,
                    exit_price=last_bar.close,
                    reason="end_of_window",
                )
            )

        returns = [trade.net_return for trade in trades]
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value < 0]
        loss_total = abs(sum(losses, Decimal("0")))
        return ProposalReplayMetrics(
            strategy_id=strategy_id,
            total_proposals=total_proposals,
            expired_proposals=expired,
            rejected_price_drift=drift_rejected,
            total_trades=len(trades),
            win_rate=float(len(wins) / len(trades)) if trades else 0.0,
            average_profit_loss_ratio=(float(mean(wins) / abs(mean(losses))) if wins and losses else None),
            profit_factor=float(sum(wins, Decimal("0")) / loss_total) if loss_total > 0 else 0.0,
            net_expectancy=float(mean(returns)) if returns else 0.0,
            net_return=float(sum(returns, Decimal("0"))),
            max_drawdown=float(_drawdown(returns)),
            funding_rate_available=self.cost_model.funding_rate_available,
            trades=tuple(trades),
        )

    def _open(self, *, proposal: StrategyProposal, bar: OHLCVBar) -> _OpenProposalPosition | None:
        expected = proposal.entry_trigger.reference_price
        drift_bps = abs(bar.open - expected) / expected * Decimal("10000")
        if drift_bps > proposal.entry_trigger.max_price_drift_bps:
            return None
        impact = self.cost_model.entry_impact_bps / Decimal("10000")
        filled_price = bar.open * (Decimal("1") + impact if proposal.side == "long" else Decimal("1") - impact)
        if proposal.side == "long" and filled_price <= proposal.invalidation.stop_price:
            return None
        if proposal.side == "short" and filled_price >= proposal.invalidation.stop_price:
            return None
        atr = abs(proposal.entry_trigger.reference_price - proposal.invalidation.stop_price)
        exit_plan = build_adaptive_exit(proposal, filled_price=filled_price, atr=max(atr, Decimal("0.00000001")))
        targets = tuple((target.label, target.price, target.quantity_fraction) for target in exit_plan.targets)
        entry_cost = self.cost_model.taker_fee_bps_per_side / Decimal("10000") * self.cost_model.partial_fill_fraction
        return _OpenProposalPosition(
            proposal=proposal,
            opened_at=bar.timestamp,
            entry_price=filled_price,
            filled_fraction=self.cost_model.partial_fill_fraction,
            initial_stop=exit_plan.initial_stop,
            targets=targets,
            remaining_fraction=self.cost_model.partial_fill_fraction,
            realized_cost=entry_cost,
        )

    def _advance(self, *, position: _OpenProposalPosition, bar: OHLCVBar) -> ProposalReplayTrade | None:
        position.bars_held += 1
        stop_hit = (
            bar.low <= position.initial_stop if position.proposal.side == "long" else bar.high >= position.initial_stop
        )
        if stop_hit:
            return self._close(
                position=position,
                closed_at=bar.timestamp,
                exit_price=position.initial_stop,
                reason="stop_loss",
            )
        for label, price, fraction in position.targets:
            if label in position.target_labels_hit:
                continue
            hit = bar.high >= price if position.proposal.side == "long" else bar.low <= price
            if hit:
                closed_fraction = min(position.remaining_fraction, fraction * position.filled_fraction)
                impact = self.cost_model.entry_impact_bps / Decimal("10000")
                realized_target = price * (
                    Decimal("1") - impact if position.proposal.side == "long" else Decimal("1") + impact
                )
                position.realized_gross += (
                    _directional_return(
                        side=position.proposal.side, entry=position.entry_price, exit_price=realized_target
                    )
                    * closed_fraction
                )
                position.realized_cost += self.cost_model.taker_fee_bps_per_side / Decimal("10000") * closed_fraction
                position.remaining_fraction -= closed_fraction
                position.target_labels_hit.append(label)
                position.last_exit_price = realized_target
        if position.remaining_fraction <= Decimal("0"):
            return self._close(position=position, closed_at=bar.timestamp, exit_price=bar.close, reason="all_targets")
        time_exit = build_adaptive_exit(
            position.proposal,
            filled_price=position.entry_price,
            atr=max(abs(position.entry_price - position.initial_stop), Decimal("0.00000001")),
        )
        if position.bars_held >= time_exit.time_exit_bars:
            return self._close(position=position, closed_at=bar.timestamp, exit_price=bar.close, reason="time_exit")
        return None

    def _close(
        self,
        *,
        position: _OpenProposalPosition,
        closed_at: datetime,
        exit_price: Decimal,
        reason: str,
    ) -> ProposalReplayTrade:
        if position.remaining_fraction <= Decimal("0") and position.last_exit_price is not None:
            realized_exit = position.last_exit_price
        else:
            impact = self.cost_model.entry_impact_bps / Decimal("10000")
            realized_exit = exit_price * (
                Decimal("1") - impact if position.proposal.side == "long" else Decimal("1") + impact
            )
        remaining_gross = (
            _directional_return(side=position.proposal.side, entry=position.entry_price, exit_price=realized_exit)
            * position.remaining_fraction
        )
        exit_cost = self.cost_model.taker_fee_bps_per_side / Decimal("10000") * position.remaining_fraction
        hold_hours = Decimal(str((closed_at - position.opened_at).total_seconds() / 3600))
        funding_intervals = max(Decimal("0"), hold_hours / self.cost_model.funding_interval_hours)
        funding = abs(self.cost_model.funding_rate_per_interval) * funding_intervals * position.filled_fraction
        gross = position.realized_gross + remaining_gross
        cost = position.realized_cost + exit_cost + funding
        return ProposalReplayTrade(
            proposal_id=position.proposal.proposal_id,
            strategy_id=position.proposal.strategy_id,
            symbol=position.proposal.symbol,
            side=position.proposal.side,
            signal_time=position.proposal.signal_bar_time,
            opened_at=position.opened_at,
            closed_at=closed_at,
            entry_price=position.entry_price,
            exit_price=realized_exit,
            stop_price=position.initial_stop,
            exit_reason=reason,
            filled_fraction=position.filled_fraction,
            gross_return=gross,
            net_return=gross - cost,
            fees_and_impact_bps=self.cost_model.explicit_round_trip_cost_bps,
            funding_cost=funding,
            target_labels_hit=tuple(position.target_labels_hit),
        )
