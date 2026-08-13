"""Entry sizing must express risk_per_trade against the stop, under hard ceilings.

Runtime evidence 2026-08-07 (armed v2_active, ETH/USDT):
``equity=7076.84, risk_per_trade=0.05, max_leverage=40`` produced a 353.84 USDT
notional (0.185 ETH, 8.85 USDT margin). Round-trip fees of 0.284 USDT consumed
23% of the 1.241 USDT gross move, and the size responded to neither the stop
distance nor the configured leverage — ``max_leverage`` was unused by sizing.

These tests lock the corrected contract:
  notional = min(equity * risk_per_trade / stop_pct,
                 equity * max_position_fraction,     # exposure ceiling
                 equity * max_leverage)              # margin capacity ceiling
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.automated_trading.application.cycle_service import CycleRequest, _calculate_quantity
from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.runtime_lock import EngineActivation

EQUITY = Decimal("7076.8360715")
PRICE = Decimal("1912.52")


class _Snapshot:
    """Minimal authoritative snapshot stand-in: sizing only reads equity."""

    def __init__(self, equity: Decimal = EQUITY) -> None:
        self.equity = equity


def _request(**overrides) -> CycleRequest:  # noqa: ANN003
    now = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)
    bar = BarView(
        timestamp=now,
        open=PRICE,
        high=PRICE,
        low=PRICE,
        close=PRICE,
        volume=Decimal("10"),
    )
    base = {
        "cycle_id": "cycle-sizing",
        "symbol": "ETH/USDT",
        "timeframe": "15m",
        "entry_timeframe": TimeframeView(timeframe="15m", bars=(bar,)),
        "execution_mode": V2ExecutionMode.BINANCE_TESTNET,
        "engine_activation": EngineActivation.ACTIVE,
        "fencing_token": "token-sizing",
        "now": now,
        "risk_per_trade": Decimal("0.10"),
        "max_leverage": 40,
        "max_margin_fraction": Decimal("0.05"),
        "max_position_fraction": Decimal("0.35"),
    }
    base.update(overrides)
    return CycleRequest(**base)


def test_sizing_uses_the_stop_distance_not_the_raw_risk_fraction() -> None:
    """A 1% stop with 10% risk must size to 10x equity in notional, then be capped."""
    stop_distance = PRICE * Decimal("0.01")  # 1% stop

    notional = _calculate_quantity(
        _request(max_position_fraction=Decimal("100"), max_margin_fraction=Decimal("100")),  # ceilings lifted
        _Snapshot(),
        stop_distance=stop_distance,
        reference_price=PRICE,
    )

    # equity * 0.10 / 0.01 == equity * 10
    assert notional == pytest.approx(EQUITY * 10, rel=Decimal("1e-9"))


def test_tighter_stop_produces_a_larger_position() -> None:
    """Risk-based sizing must be inversely proportional to stop distance."""
    wide = _calculate_quantity(
        _request(max_position_fraction=Decimal("100"), max_margin_fraction=Decimal("100")),
        _Snapshot(),
        stop_distance=PRICE * Decimal("0.04"),
        reference_price=PRICE,
    )
    tight = _calculate_quantity(
        _request(max_position_fraction=Decimal("100"), max_margin_fraction=Decimal("100")),
        _Snapshot(),
        stop_distance=PRICE * Decimal("0.01"),
        reference_price=PRICE,
    )

    assert tight == pytest.approx(wide * 4, rel=Decimal("1e-9"))


def test_exposure_cap_is_a_hard_ceiling() -> None:
    """max_position_fraction must never be exceeded, however tight the stop."""
    notional = _calculate_quantity(
        _request(),  # 0.35 exposure cap
        _Snapshot(),
        stop_distance=PRICE * Decimal("0.0035"),  # very tight stop
        reference_price=PRICE,
    )

    assert notional == EQUITY * Decimal("0.35")


def test_margin_capacity_is_a_hard_ceiling() -> None:
    """A notional above equity * max_leverage is unfundable and must be clamped."""
    notional = _calculate_quantity(
        _request(max_leverage=2, max_position_fraction=Decimal("100"), max_margin_fraction=Decimal("100")),
        _Snapshot(),
        stop_distance=PRICE * Decimal("0.0001"),  # absurdly tight -> huge risk notional
        reference_price=PRICE,
    )

    assert notional == EQUITY * Decimal("2")


def test_margin_budget_is_a_hard_ceiling_separate_from_risk_budget() -> None:
    """A 5% margin budget at 50x caps each new entry at 2.5x equity notional."""
    notional = _calculate_quantity(
        _request(max_leverage=50, max_position_fraction=Decimal("2.50")),
        _Snapshot(),
        stop_distance=PRICE * Decimal("0.0001"),
        reference_price=PRICE,
    )

    assert notional == EQUITY * Decimal("0.05") * Decimal("50")


def test_missing_stop_geometry_falls_back_to_the_small_conservative_branch() -> None:
    """Without a measured stop, sizing must not silently scale up."""
    without_stop = _calculate_quantity(_request(), _Snapshot())
    assert without_stop == EQUITY * Decimal("0.10")

    for bad in (Decimal("0"), Decimal("-1")):
        assert _calculate_quantity(
            _request(), _Snapshot(), stop_distance=bad, reference_price=PRICE
        ) == EQUITY * Decimal("0.10")
        assert _calculate_quantity(
            _request(), _Snapshot(), stop_distance=PRICE * Decimal("0.01"), reference_price=bad
        ) == EQUITY * Decimal("0.10")


def test_operator_fixed_notional_still_wins_and_still_respects_ceilings() -> None:
    """An explicit notional bypasses risk math but never the ceilings."""
    pinned = _calculate_quantity(
        _request(order_notional_usdt=Decimal("500")),
        _Snapshot(),
        stop_distance=PRICE * Decimal("0.01"),
        reference_price=PRICE,
    )
    assert pinned == Decimal("500")

    clamped = _calculate_quantity(
        _request(order_notional_usdt=Decimal("999999"), max_position_fraction=Decimal("0.35")),
        _Snapshot(),
        stop_distance=PRICE * Decimal("0.01"),
        reference_price=PRICE,
    )
    assert clamped == EQUITY * Decimal("0.35")


def test_observed_regression_case_is_no_longer_dust() -> None:
    """The exact 2026-08-07 inputs must now size meaningfully, not to 0.185 ETH."""
    stop_distance = Decimal("6.684685")  # the real candidate stop distance

    notional = _calculate_quantity(
        _request(),
        _Snapshot(),
        stop_distance=stop_distance,
        reference_price=PRICE,
    )
    quantity = notional / PRICE

    # The old implementation produced 353.84 USDT / 0.185 ETH.
    assert notional > Decimal("2000")
    assert quantity > Decimal("1.0")
    # And it must still respect the operator exposure cap exactly.
    assert notional == EQUITY * Decimal("0.35")
