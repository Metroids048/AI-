"""V2 trade candidate contract: relative risk plan, never stale absolute prices.

Design rule (plan section 3.4):
The strategy layer emits only *relative* risk geometry (distances), plus a
reference price and a drift tolerance. Absolute stop/take-profit prices are
computed by the execution layer AFTER a real exchange fill, from
``average_fill_price``.

This removes an entire class of bugs where a stop price computed from a stale
decision-time price was submitted to the exchange minutes later.

Invariants enforced here:
- stop_distance > 0 (a candidate without a stop can never be executable)
- take_profit_distance is either None (trail-only) or > 0
- max_entry_drift_bps > 0
- expires_at > signal_candle_close_time
- SAMPLING candidates are always non_promotable
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from services.automated_trading.domain.enums import V2CandidateType

# The automated execution universe is fixed (Exchange-First invariant #1).
# Research coverage does not grant execution permission.
EXECUTION_UNIVERSE: frozenset[str] = frozenset({"BTC/USDT", "ETH/USDT"})


class CandidateLane(StrEnum):
    """Lane labels. Production results feed strategy evidence; sampling never does."""

    PRODUCTION = "PRODUCTION"
    TESTNET_SAMPLING = "TESTNET_SAMPLING"


class CandidateSide(StrEnum):
    """Directional side of a candidate."""

    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class TradeCandidate:
    """Immutable strategy output: a relative risk plan for one symbol/bar.

    candidate_id: Unique id for this candidate instance.
    cycle_id: Owning execution cycle.
    strategy_id / strategy_version: Provenance for attribution.
    lane: PRODUCTION or TESTNET_SAMPLING.
    candidate_type: PRIMARY / SAMPLING / RESEARCH (promotion semantics).
    symbol: Execution universe is BTC/USDT and ETH/USDT only.
    side: "LONG" | "SHORT".
    signal_candle_close_time: Close time of the bar this decision was made on.
    signal_reference_price: Close price used by the strategy (NOT an execution price).
    confidence: Strategy confidence in [0, 1].
    stop_distance: Absolute price distance for the stop, in quote currency.
    take_profit_distance: Absolute price distance for TP, or None for trail-only.
    max_entry_drift_bps: Max tolerated drift between reference and pre-submit price.
    expires_at: Candidate must not be submitted after this instant.
    non_promotable: True => results must never enter strategy promotion evidence.
    """

    candidate_id: str
    cycle_id: str
    strategy_id: str
    strategy_version: str
    lane: str
    candidate_type: V2CandidateType
    symbol: str
    side: str
    signal_candle_close_time: datetime
    signal_reference_price: Decimal
    confidence: Decimal
    stop_distance: Decimal
    take_profit_distance: Decimal | None
    max_entry_drift_bps: Decimal
    expires_at: datetime
    non_promotable: bool
    signal_context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError(f"TradeCandidate side must be LONG or SHORT, got {self.side!r}")
        if self.lane not in {CandidateLane.PRODUCTION, CandidateLane.TESTNET_SAMPLING}:
            raise ValueError(f"TradeCandidate lane must be PRODUCTION or TESTNET_SAMPLING, got {self.lane!r}")
        if self.signal_reference_price <= 0:
            raise ValueError(f"signal_reference_price must be > 0, got {self.signal_reference_price}")
        if self.stop_distance <= 0:
            raise ValueError(f"stop_distance must be > 0, got {self.stop_distance}")
        if self.take_profit_distance is not None and self.take_profit_distance <= 0:
            raise ValueError(f"take_profit_distance must be > 0 when set, got {self.take_profit_distance}")
        if self.max_entry_drift_bps <= 0:
            raise ValueError(f"max_entry_drift_bps must be > 0, got {self.max_entry_drift_bps}")
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence}")
        if self.expires_at <= self.signal_candle_close_time:
            raise ValueError("expires_at must be after signal_candle_close_time")
        if self.candidate_type is V2CandidateType.SAMPLING and not self.non_promotable:
            raise ValueError("SAMPLING candidates must be non_promotable")
        if self.lane == CandidateLane.TESTNET_SAMPLING and not self.non_promotable:
            raise ValueError("TESTNET_SAMPLING candidates must be non_promotable")

    @property
    def direction(self) -> str:
        """Lowercase direction as used by persistence and position records."""
        return "long" if self.side == "LONG" else "short"

    def is_expired(self, now: datetime) -> bool:
        """True when this candidate must no longer be submitted."""
        return now >= self.expires_at

    def resolve_protection_prices(self, average_fill_price: Decimal) -> tuple[Decimal, Decimal | None]:
        """Compute absolute stop/take-profit from a REAL exchange fill price.

        This is the only sanctioned way to obtain absolute protection prices.
        Callers must pass ``average_fill_price`` from an exchange fill receipt,
        never a decision-time or reference price.

        Returns:
            (stop_loss_price, take_profit_price_or_None)
        """
        if average_fill_price <= 0:
            raise ValueError(f"average_fill_price must be > 0, got {average_fill_price}")

        if self.side == "LONG":
            stop = average_fill_price - self.stop_distance
            take = average_fill_price + self.take_profit_distance if self.take_profit_distance else None
        else:
            stop = average_fill_price + self.stop_distance
            take = average_fill_price - self.take_profit_distance if self.take_profit_distance else None

        if stop <= 0:
            raise ValueError(
                f"resolved stop price must be > 0 (fill={average_fill_price}, distance={self.stop_distance})"
            )
        if take is not None and take <= 0:
            raise ValueError(
                f"resolved take-profit price must be > 0 (fill={average_fill_price}, "
                f"distance={self.take_profit_distance})"
            )
        return stop, take

    def drift_bps_from(self, pre_submit_price: Decimal) -> Decimal:
        """Absolute drift in basis points between reference price and a live price."""
        if pre_submit_price <= 0:
            raise ValueError(f"pre_submit_price must be > 0, got {pre_submit_price}")
        delta = abs(pre_submit_price - self.signal_reference_price)
        return (delta / self.signal_reference_price) * Decimal("10000")

    def drift_within_tolerance(self, pre_submit_price: Decimal) -> bool:
        """True when live price has not drifted beyond the candidate's tolerance."""
        return self.drift_bps_from(pre_submit_price) <= self.max_entry_drift_bps
