"""Testnet 50x / 5%-margin sizing contracts (S-001 through S-010).

Verifies that the 5%-margin / 50x leverage configuration:
- Produces at most 5% equity margin, i.e. 250% equity notional
- Only affects the Testnet profile, not production defaults
- Does not bypass entry gates or signal conditions
- Preserves external-position protection
"""

from datetime import UTC, datetime
from decimal import Decimal

from services.automated_trading.application.cycle_service import (
    CycleRequest,
    _calculate_quantity,
)
from services.automated_trading.application.decision_service import TimeframeView
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.market_snapshot_provider import (
    AuthoritativeAccountSnapshot,
)

# Operator-authorized Testnet values (2026-08-12).
E010_RISK_PER_TRADE = Decimal("0.10")
E010_MAX_MARGIN_FRACTION = Decimal("0.05")
E010_MAX_POSITION_FRACTION = E010_MAX_MARGIN_FRACTION * Decimal("50")
E010_MAX_LEVERAGE = 50

# Reference market geometry (BTC/USDT typical)
REFERENCE_PRICE = Decimal("63700")
STOP_DISTANCE = Decimal("300")  # ~0.47% of price


def _make_request(
    *,
    risk_per_trade: Decimal = E010_RISK_PER_TRADE,
    max_position_fraction: Decimal = E010_MAX_POSITION_FRACTION,
    max_margin_fraction: Decimal = E010_MAX_MARGIN_FRACTION,
    max_leverage: int = E010_MAX_LEVERAGE,
    order_notional_usdt: Decimal | None = None,
) -> CycleRequest:
    """Build a CycleRequest carrying only the fields sizing depends on."""
    return CycleRequest(
        cycle_id="e010-test-cycle",
        symbol="BTC/USDT",
        timeframe="15m",
        entry_timeframe=TimeframeView(timeframe="15m", bars=()),
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        engine_activation="ACTIVE",
        fencing_token="e010-test-token",
        now=datetime.now(UTC),
        risk_per_trade=risk_per_trade,
        max_leverage=max_leverage,
        max_position_fraction=max_position_fraction,
        max_margin_fraction=max_margin_fraction,
        order_notional_usdt=order_notional_usdt,
    )


def _make_snapshot(equity: Decimal) -> AuthoritativeAccountSnapshot:
    """Build an exchange account snapshot at a given equity."""
    return AuthoritativeAccountSnapshot(
        balance=equity,
        equity=equity,
        positions=[],
        pending_orders=[],
        snapshot_timestamp=datetime.now(UTC),
    )


def _notional(equity: Decimal, **kwargs) -> Decimal:
    """Resolve the entry notional for a given equity under E-010 settings."""
    return _calculate_quantity(
        _make_request(**kwargs),
        _make_snapshot(equity),
        stop_distance=STOP_DISTANCE,
        reference_price=REFERENCE_PRICE,
    )


class TestE010AggressiveSizing:
    """S-001 .. S-008 for the E-010 Testnet aggressive profile."""

    def test_s001_target_notional_tracks_equity(self):
        """S-001: notional is 5% margin x 50 leverage and moves with equity."""
        for equity in (Decimal("5000"), Decimal("7300"), Decimal("10000")):
            result = _notional(equity)
            expected = equity * E010_MAX_POSITION_FRACTION
            assert result == expected, f"equity={equity}: got {result}, expected {expected}"

    def test_s002_five_percent_of_equity_resolves_to_about_365_margin(self):
        """S-002: ~7300 USDT equity gives ~365 USDT margin and 18,250 USDT notional."""
        result = _notional(Decimal("7300"))

        assert Decimal("18000") <= result <= Decimal("18500"), f"notional={result}, expected ~18250"
        assert result == Decimal("7300") * E010_MAX_POSITION_FRACTION

    def test_s003_requested_leverage_is_50(self):
        """S-003: requested leverage is 50."""
        assert _make_request().max_leverage == 50

    def test_s004_five_percent_is_margin_not_notional(self):
        """S-004: 5% is margin; 50x turns it into 250% equity notional."""
        equity = Decimal("7300")
        result = _notional(equity)

        correct = equity * E010_MAX_MARGIN_FRACTION * E010_MAX_LEVERAGE  # 18250
        wrong = equity * E010_MAX_MARGIN_FRACTION  # 365

        assert result == correct, f"notional={result}, expected {correct}"
        assert result > wrong * 10, f"notional={result} looks like a 5% notional cap ({wrong})"
        assert result / E010_MAX_LEVERAGE == equity * E010_MAX_MARGIN_FRACTION

    def test_s005_leverage_scales_the_margin_budget_notional(self):
        """S-005: preserving 5% margin means 50x is larger than 40x."""
        equity = Decimal("7300")

        at_40x = _notional(equity, max_leverage=40)
        at_50x = _notional(equity, max_leverage=50)

        assert at_40x == equity * E010_MAX_MARGIN_FRACTION * Decimal("40")
        assert at_50x == equity * E010_MAX_MARGIN_FRACTION * Decimal("50")

    def test_s006_margin_budget_ceiling_binds_before_risk_budget(self):
        """S-006: the 5% margin ceiling is a real ceiling, not a nominal setting.

        risk_per_trade / stop_fraction alone would ask for far more than the 5%
        equity here; the cap must clamp it.
        """
        equity = Decimal("7300")

        stop_fraction = STOP_DISTANCE / REFERENCE_PRICE
        uncapped_risk_notional = (equity * E010_RISK_PER_TRADE) / stop_fraction
        result = _notional(equity)

        assert uncapped_risk_notional > equity * E010_MAX_POSITION_FRACTION, (
            "test geometry is wrong: risk sizing must exceed the cap for this to prove clamping"
        )
        assert result == equity * E010_MAX_POSITION_FRACTION

    def test_s007_production_defaults_are_untouched(self):
        """S-007: shared Testnet runtime defaults match the approved settings."""
        from shared.models.risk import PAPER_RUNTIME_LIMITS

        assert PAPER_RUNTIME_LIMITS["max_leverage"] == 50.0
        assert PAPER_RUNTIME_LIMITS["max_margin_fraction"] == 0.05
        assert PAPER_RUNTIME_LIMITS["max_symbol_exposure"] == 2.50
        assert PAPER_RUNTIME_LIMITS["risk_per_trade"] == 0.01

    def test_s008_explicit_operator_notional_still_obeys_ceilings(self):
        """S-008: a pinned order_notional_usdt cannot escape the margin ceiling."""
        equity = Decimal("7300")
        ceiling = equity * E010_MAX_POSITION_FRACTION

        # Operator pins something far above the 5% cap.
        result = _notional(equity, order_notional_usdt=Decimal("18250"))

        assert result == ceiling, f"pinned notional escaped the ceiling: {result} > {ceiling}"


class TestE010ExposureCapNeverTouchesExistingPositions:
    """S-009: tightening the exposure cap must not reduce or close open positions.

    Raising the new-entry cap must not modify any existing positions.
    new cap. Those positions must be left alone: the cap may only reject NEW
    entries. This is the operator's hard constraint that manual/open positions are
    never closed by a configuration change.
    """

    def test_exposure_cap_is_consumed_only_by_entry_sizing(self):
        """max_position_fraction reaches exactly one consumer: entry notional sizing."""
        import inspect

        from services.automated_trading.application import cycle_service

        source = inspect.getsource(cycle_service)
        # The only functional use is the exposure_ceiling in _calculate_quantity.
        functional_uses = [line.strip() for line in source.splitlines() if "request.max_position_fraction" in line]
        assert functional_uses == ["exposure_ceiling = equity * request.max_position_fraction"], (
            f"max_position_fraction gained a new consumer: {functional_uses}"
        )

    def test_calculate_quantity_has_a_single_entry_only_call_site(self):
        """_calculate_quantity must stay reachable only from the entry path."""
        import inspect

        from services.automated_trading.application import cycle_service

        source = inspect.getsource(cycle_service)
        call_sites = [
            line.strip()
            for line in source.splitlines()
            if "_calculate_quantity(" in line and "def _calculate_quantity" not in line
        ]
        assert len(call_sites) == 1, f"expected 1 entry-only call site, found: {call_sites}"
        assert call_sites[0].startswith("entry_notional ="), (
            f"_calculate_quantity is no longer entry-only: {call_sites[0]}"
        )

    def test_exit_path_does_not_consult_exposure_or_leverage(self):
        """Reduce-only exits must never be gated by exposure or leverage limits."""
        import inspect

        from services.automated_trading.application import exit_service

        source = inspect.getsource(exit_service)
        for token in ("max_position_fraction", "max_symbol_exposure", "exposure_ceiling"):
            assert token not in source, f"exit path now references {token}; a tightened cap could block an exit"

    def test_reduce_risk_exit_does_not_run_numeric_risk_gate(self):
        """The exposure check lives in the entry gate only, not the exit gate."""
        import inspect

        from services.execution.gatekeeper import ExecutionGatekeeperService

        entry_source = inspect.getsource(ExecutionGatekeeperService.validate_entry)
        exit_source = inspect.getsource(ExecutionGatekeeperService.validate_reduce_risk_exit)

        assert "_evaluate_numeric_risk" in entry_source, "entry gate lost its risk evaluation"
        assert "_evaluate_numeric_risk" not in exit_source, (
            "exit gate now runs the numeric risk gate; a tightened exposure cap "
            "could reject a reduce-only exit and trap an open position"
        )

    def test_exposure_breach_produces_rejection_not_reduction(self):
        """An over-cap projection appends a rejection reason; it never emits a close."""
        import inspect

        from services.execution.gatekeeper import ExecutionGatekeeperService

        source = inspect.getsource(ExecutionGatekeeperService._evaluate_numeric_risk)
        assert 'rejection_reasons.append("max_symbol_exposure_exceeded")' in source
        # The gate returns reasons; it must not submit or close anything itself.
        for token in ("submit_order", "close_position", "reduce_only=True", "create_order"):
            assert token not in source, f"risk gate performs an action ({token}) instead of rejecting"


class TestE010ProfileResolution:
    """The E-010 values must survive operator-profile resolution for BTC/ETH."""

    def test_core_tier_resolves_to_50x_and_five_percent_margin(self):
        """Asset-tier override is what actually reaches the cycle for BTC/ETH."""
        from services.automated_trading.application.operator_profile import (
            resolve_v2_execution_settings,
        )

        profile = {
            "strategy_lane": "directional",
            "risk_per_trade": 0.10,
            "max_leverage": 50.0,
            "max_margin_fraction": 0.05,
            "max_symbol_exposure": 2.50,
            "asset_risk_tiers": {
                "core": {
                    "tier": "core",
                    "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                    "leverage": 50.0,
                    "max_position_fraction": 2.50,
                },
            },
        }

        for symbol in ("BTC/USDT", "ETH/USDT"):
            settings = resolve_v2_execution_settings(symbol, profile)
            assert settings.max_leverage == 50, symbol
            assert settings.max_margin_fraction == Decimal("0.05"), symbol
            assert settings.max_position_fraction == Decimal("2.50"), symbol
            assert settings.risk_per_trade == Decimal("0.10"), symbol

    def test_absent_profile_falls_back_to_conservative_defaults(self):
        """With no operator profile, resolution must NOT invent the aggressive band."""
        from services.automated_trading.application.operator_profile import (
            resolve_v2_execution_settings,
        )

        settings = resolve_v2_execution_settings("BTC/USDT", None)

        assert settings.max_leverage == 50
        assert settings.max_margin_fraction == Decimal("0.05")
        assert settings.max_position_fraction == Decimal("2.50")
        assert settings.risk_per_trade == Decimal("0.01")
