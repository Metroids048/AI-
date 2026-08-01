from decimal import Decimal
from typing import Literal

from services.validation.costs import estimate_round_trip_cost_bps


def _funding_cost(*, rate: str, side: Literal["long", "short"]) -> Decimal:
    return estimate_round_trip_cost_bps(
        spot_entry=Decimal("100"),
        spot_exit=Decimal("100"),
        perp_entry=Decimal("100"),
        perp_exit=Decimal("100"),
        funding_rate=Decimal(rate),
        position_side=side,
    ).funding_bps


def test_funding_cost_uses_position_side_and_signed_rate() -> None:
    assert _funding_cost(rate="0.001", side="long") == Decimal("10")
    assert _funding_cost(rate="0.001", side="short") == Decimal("-10")
    assert _funding_cost(rate="-0.001", side="long") == Decimal("-10")
    assert _funding_cost(rate="-0.001", side="short") == Decimal("10")
