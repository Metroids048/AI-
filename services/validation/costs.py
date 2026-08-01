"""Backtest cost modeling helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class CostBreakdown:
    fee_bps: Decimal
    slippage_bps: Decimal
    funding_bps: Decimal

    @property
    def total_bps(self) -> Decimal:
        return self.fee_bps + self.slippage_bps + self.funding_bps

    def as_float_dict(self) -> dict[str, float]:
        return {
            "fee_bps": float(self.fee_bps),
            "slippage_bps": float(self.slippage_bps),
            "funding_bps": float(self.funding_bps),
        }


def estimate_round_trip_cost_bps(
    *,
    spot_entry: Decimal,
    spot_exit: Decimal,
    perp_entry: Decimal,
    perp_exit: Decimal,
    funding_rate: Decimal,
    maker_fee_bps: Decimal = Decimal("2"),
    taker_fee_bps: Decimal = Decimal("4"),
    min_slippage_bps: Decimal = Decimal("1"),
    position_side: Literal["long", "short"] | None = None,
) -> CostBreakdown:
    """Estimate two-leg carry trade costs without order-book depth.

    Until depth snapshots are persisted, slippage uses observed spot/perp basis
    at entry and exit plus a conservative minimum. Funding is reported as a
    separate signed benefit/cost in bps and is handled by the strategy return
    calculation; here it is exposed for auditability.
    """

    fee_bps = (maker_fee_bps + taker_fee_bps) * Decimal("2")
    entry_basis_bps = abs(perp_entry - spot_entry) / spot_entry * Decimal("10000")
    exit_basis_bps = abs(perp_exit - spot_exit) / spot_exit * Decimal("10000")
    slippage_bps = max(min_slippage_bps, (entry_basis_bps + exit_basis_bps) / Decimal("2"))
    effective_side = position_side or ("short" if funding_rate > 0 else "long")
    funding_bps = (funding_rate if effective_side == "long" else -funding_rate) * Decimal("10000")
    return CostBreakdown(
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        funding_bps=funding_bps,
    )
