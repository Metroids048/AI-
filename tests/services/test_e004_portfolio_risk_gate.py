"""E-004 portfolio / crypto-cluster initial-risk gate contracts."""

from __future__ import annotations

from decimal import Decimal

from services.automated_trading.domain.portfolio_risk import (
    MAX_SAME_DIRECTION_CLUSTER_RISK_FRACTION,
    MAX_TOTAL_INITIAL_RISK_FRACTION,
    RiskExposure,
    evaluate_portfolio_risk,
    portfolio_risk_blocks,
)

EQUITY = Decimal("7000")


def _decide(candidate_risk: Decimal, committed: list[RiskExposure], **kwargs: object):
    return evaluate_portfolio_risk(
        equity=EQUITY,
        candidate_symbol=str(kwargs.get("symbol", "BTC/USDT")),
        candidate_direction=str(kwargs.get("direction", "short")),
        candidate_initial_risk_usdt=candidate_risk,
        committed=committed,
    )


def test_single_candidate_within_budget_is_allowed() -> None:
    decision = _decide(Decimal("30"), [])

    assert decision.allowed is True
    assert decision.reason_code is None


def test_candidate_breaching_total_budget_is_blocked() -> None:
    committed = [RiskExposure("ETH/USDT", "long", Decimal("60"))]
    decision = _decide(Decimal("40"), committed, direction="short")

    assert decision.blocked is True
    assert decision.reason_code == "PORTFOLIO_TOTAL_RISK_LIMIT"
    assert decision.projected_total_risk_fraction > MAX_TOTAL_INITIAL_RISK_FRACTION


def test_same_direction_cluster_budget_blocks_correlated_stacking() -> None:
    committed = [RiskExposure("ETH/USDT", "short", Decimal("40"))]
    decision = _decide(Decimal("30"), committed, symbol="BTC/USDT", direction="short")

    assert decision.blocked is True
    assert decision.reason_code == "CRYPTO_CLUSTER_RISK_LIMIT"
    assert decision.projected_cluster_risk_fraction > MAX_SAME_DIRECTION_CLUSTER_RISK_FRACTION


def test_opposite_direction_does_not_consume_cluster_budget() -> None:
    committed = [RiskExposure("ETH/USDT", "long", Decimal("40"))]
    decision = _decide(Decimal("30"), committed, symbol="BTC/USDT", direction="short")

    assert decision.allowed is True
    assert decision.committed_cluster_risk_usdt == Decimal("0")


def test_pending_intent_consumes_the_same_budget_as_a_position() -> None:
    """Two concurrent cycles must not both spend the same cluster budget."""
    pending = [RiskExposure("ETH/USDT", "short", Decimal("40"), source="intent")]
    decision = _decide(Decimal("30"), pending, symbol="BTC/USDT", direction="short")

    assert decision.blocked is True
    assert decision.reason_code == "CRYPTO_CLUSTER_RISK_LIMIT"


def test_terminal_intents_release_budget_by_absence() -> None:
    decision = _decide(Decimal("50"), [])

    assert decision.allowed is True
    assert decision.committed_total_risk_usdt == Decimal("0")


def test_max_open_positions_blocks_a_third_entry() -> None:
    committed = [
        RiskExposure("BTC/USDT", "short", Decimal("10")),
        RiskExposure("ETH/USDT", "long", Decimal("10")),
    ]
    decision = _decide(Decimal("5"), committed, symbol="SOL/USDT", direction="short")

    assert decision.blocked is True
    assert decision.reason_code == "MAX_OPEN_EXPOSURES"


def test_canary_allows_a_fifth_position_but_blocks_a_sixth() -> None:
    committed = [
        RiskExposure(symbol, "long", Decimal("10")) for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT")
    ]
    fifth = evaluate_portfolio_risk(
        equity=EQUITY,
        candidate_symbol="BNB/USDT",
        candidate_direction="long",
        candidate_initial_risk_usdt=Decimal("5"),
        committed=committed,
        max_open_positions=5,
    )
    assert fifth.allowed is True
    assert fifth.open_position_count == 4

    # The Testnet Canary passes its explicit five-position cap into this domain
    # function; E-004's production default remains two.
    sixth = evaluate_portfolio_risk(
        equity=EQUITY,
        candidate_symbol="BTC/USDT",
        candidate_direction="long",
        candidate_initial_risk_usdt=Decimal("5"),
        committed=committed + [RiskExposure("BNB/USDT", "long", Decimal("10"))],
        max_open_positions=5,
    )
    assert sixth.reason_code == "MAX_OPEN_EXPOSURES"


def test_canary_portfolio_limits_are_diagnostic_except_hard_position_cap() -> None:
    total = _decide(Decimal("40"), [RiskExposure("ETH/USDT", "long", Decimal("60"))], direction="short")
    assert total.reason_code == "PORTFOLIO_TOTAL_RISK_LIMIT"
    assert portfolio_risk_blocks(total, diagnostic=True) is False
    assert portfolio_risk_blocks(total, diagnostic=False) is True

    positions = evaluate_portfolio_risk(
        equity=EQUITY,
        candidate_symbol="BTC/USDT",
        candidate_direction="long",
        candidate_initial_risk_usdt=Decimal("5"),
        committed=[RiskExposure(str(i), "long", Decimal("1")) for i in range(5)],
        max_open_positions=5,
    )
    assert positions.reason_code == "MAX_OPEN_EXPOSURES"
    assert portfolio_risk_blocks(positions, diagnostic=True) is True


def test_pending_intents_do_not_count_toward_open_position_limit() -> None:
    committed = [
        RiskExposure("BTC/USDT", "short", Decimal("10"), source="intent"),
        RiskExposure("ETH/USDT", "long", Decimal("10"), source="intent"),
    ]
    decision = _decide(Decimal("5"), committed, symbol="SOL/USDT", direction="short")

    assert decision.reason_code != "PORTFOLIO_MAX_OPEN_POSITIONS"


def test_unmeasurable_risk_is_fail_closed() -> None:
    decision = _decide(Decimal("0"), [])

    assert decision.blocked is True
    assert decision.reason_code == "PORTFOLIO_RISK_UNMEASURABLE"


def test_non_positive_equity_is_fail_closed() -> None:
    decision = evaluate_portfolio_risk(
        equity=Decimal("0"),
        candidate_symbol="BTC/USDT",
        candidate_direction="short",
        candidate_initial_risk_usdt=Decimal("10"),
        committed=[],
    )

    assert decision.blocked is True
    assert decision.reason_code == "PORTFOLIO_EQUITY_UNAVAILABLE"


def test_gate_module_never_references_exit_or_protection_paths() -> None:
    """Structural guard: the gate must stay entry-only."""
    import inspect

    from services.automated_trading.domain import portfolio_risk

    source = inspect.getsource(portfolio_risk)
    for forbidden in ("reduce_only", "protection_", "emergency_close", "reconcil"):
        assert forbidden not in source.replace("reconciliation, recovery", ""), forbidden
