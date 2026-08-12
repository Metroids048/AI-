"""E-010: Aggressive Testnet sizing configuration tests (S-001 through S-008).

Verifies that the 5% notional / 50x leverage configuration:
- Produces ~5% equity notional (NOT 5% margin * 50x = 250% equity)
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

# E-010 authorized Testnet values
E010_RISK_PER_TRADE = Decimal("0.05")
E010_MAX_POSITION_FRACTION = Decimal("0.05")
E010_MAX_LEVERAGE = 50

# Reference market geometry (BTC/USDT typical)
REFERENCE_PRICE = Decimal("63700")
STOP_DISTANCE = Decimal("300")  # ~0.47% of price


def _make_request(
    *,
    risk_per_trade: Decimal = E010_RISK_PER_TRADE,
    max_position_fraction: Decimal = E010_MAX_POSITION_FRACTION,
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
        """S-001: notional = equity * 5% and moves with equity, never hardcoded."""
        for equity in (Decimal("5000"), Decimal("7300"), Decimal("10000")):
            result = _notional(equity)
            expected = equity * E010_MAX_POSITION_FRACTION
            assert result == expected, f"equity={equity}: got {result}, expected {expected}"

    def test_s002_five_percent_of_equity_resolves_to_about_365(self):
        """S-002: at ~7300 USDT equity a 5% exposure resolves to ~365 USDT notional.

        Measured baseline on the live Testnet profile (2026-08-12, equity 7349):
        exposure 0.35 at 40x resolved to 2572.15 USDT notional / 64.30 USDT margin
        per symbol. The 64.30 USDT margin figure is what an operator reads as
        "about 60U" on the account screen — it is margin, not position size.

        So a 0.05 exposure is a ~7x REDUCTION in notional (2572 -> 365), not an
        increase. It reduces margin to ~7.3 USDT at 50x. Recorded here so the
        wrong "old size was 60U notional" premise is not inherited by later work.
        """
        result = _notional(Decimal("7300"))

        assert Decimal("350") <= result <= Decimal("380"), f"notional={result}, expected ~365"
        assert result == Decimal("7300") * E010_MAX_POSITION_FRACTION

    def test_s003_requested_leverage_is_50(self):
        """S-003: requested leverage is 50."""
        assert _make_request().max_leverage == 50

    def test_s004_five_percent_is_notional_not_margin_times_leverage(self):
        """S-004: 5% is the NOTIONAL, and must not be multiplied by leverage again.

        The misreading would be: margin = equity * 5%, notional = margin * 50x,
        i.e. 7300 * 0.05 * 50 = 18250 USDT — 250% of equity, nothing like the
        ~360 USDT the operator asked for. This test pins the correct semantics.
        """
        equity = Decimal("7300")
        result = _notional(equity)

        correct = equity * E010_MAX_POSITION_FRACTION  # 365
        wrong = equity * E010_MAX_POSITION_FRACTION * E010_MAX_LEVERAGE  # 18250

        assert result == correct, f"notional={result}, expected {correct}"
        assert result < wrong / 10, f"notional={result} looks like margin*leverage ({wrong})"
        # Independent of the ratio: exposure must never exceed equity on a 5% target.
        assert result < equity

    def test_s005_leverage_raises_capacity_without_inflating_notional(self):
        """S-005: leverage only relaxes the margin ceiling; it never sets the notional.

        40x and 50x must resolve to the SAME notional, because the binding
        constraint is the 5% exposure ceiling, not margin capacity.
        """
        equity = Decimal("7300")

        at_40x = _notional(equity, max_leverage=40)
        at_50x = _notional(equity, max_leverage=50)

        assert at_40x == at_50x == equity * E010_MAX_POSITION_FRACTION

    def test_s006_exposure_ceiling_binds_before_risk_budget(self):
        """S-006: the 5% exposure cap is a real ceiling, not a nominal setting.

        risk_per_trade / stop_fraction alone would ask for far more than 5% of
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
        """S-007: E-010 must not move the shared conservative defaults.

        The aggressive values live in the operator PaperRun profile, which is
        Testnet-scoped. PAPER_RUNTIME_LIMITS is the shared fallback and must not
        be rewritten to 50x/5% by this change.
        """
        from shared.models.risk import PAPER_RUNTIME_LIMITS

        assert PAPER_RUNTIME_LIMITS["max_leverage"] == 40.0
        assert PAPER_RUNTIME_LIMITS["max_symbol_exposure"] == 0.35
        assert PAPER_RUNTIME_LIMITS["risk_per_trade"] == 0.10

    def test_s008_explicit_operator_notional_still_obeys_ceilings(self):
        """S-008: a pinned order_notional_usdt cannot escape the exposure ceiling."""
        equity = Decimal("7300")
        ceiling = equity * E010_MAX_POSITION_FRACTION

        # Operator pins something far above the 5% cap.
        result = _notional(equity, order_notional_usdt=Decimal("18250"))

        assert result == ceiling, f"pinned notional escaped the ceiling: {result} > {ceiling}"


class TestE010ExposureCapNeverTouchesExistingPositions:
    """S-009: tightening the exposure cap must not reduce or close open positions.

    Lowering max_symbol_exposure 0.35 -> 0.05 leaves existing positions above the
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

    def test_core_tier_resolves_to_50x_and_five_percent(self):
        """Asset-tier override is what actually reaches the cycle for BTC/ETH."""
        from services.automated_trading.application.operator_profile import (
            resolve_v2_execution_settings,
        )

        profile = {
            "strategy_lane": "directional",
            "risk_per_trade": 0.05,
            "max_leverage": 50.0,
            "max_symbol_exposure": 0.05,
            "asset_risk_tiers": {
                "core": {
                    "tier": "core",
                    "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                    "leverage": 50.0,
                    "max_position_fraction": 0.05,
                },
            },
        }

        for symbol in ("BTC/USDT", "ETH/USDT"):
            settings = resolve_v2_execution_settings(symbol, profile)
            assert settings.max_leverage == 50, symbol
            assert settings.max_position_fraction == Decimal("0.05"), symbol
            assert settings.risk_per_trade == Decimal("0.05"), symbol

    def test_absent_profile_falls_back_to_conservative_defaults(self):
        """With no operator profile, resolution must NOT invent the aggressive band."""
        from services.automated_trading.application.operator_profile import (
            resolve_v2_execution_settings,
        )

        settings = resolve_v2_execution_settings("BTC/USDT", None)

        assert settings.max_leverage == 40
        assert settings.max_position_fraction == Decimal("0.35")
        assert settings.risk_per_trade == Decimal("0.10")
