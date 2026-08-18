"""E-004 portfolio and crypto-cluster initial-risk budget.

Pure domain logic: no database, no exchange, no side effects. The gate answers one
question only — may NEW exposure be opened right now — and it answers it in
initial stop-loss risk, not notional, because notional understates how correlated
a five-symbol crypto book really is.

Contract boundaries that must not drift:

* Reduce-only exits, protection submission/replacement, reconciliation, recovery,
  and emergency close never consult this module. It is entry-only by construction.
* In-flight entry intents consume budget exactly like open positions do. Two
  concurrent symbol cycles must not each believe the same budget is free.
* Terminal intent states release their reservation implicitly by no longer being
  in-flight, so a failed or cancelled attempt cannot strand a ghost reservation.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

# Validation-phase ceilings. These may be lowered by evidence; raising them is a
# risk decision that belongs to the operator, not to this module.
MAX_TOTAL_INITIAL_RISK_FRACTION = Decimal("0.0125")
MAX_SAME_DIRECTION_CLUSTER_RISK_FRACTION = Decimal("0.0080")
MAX_OPEN_POSITIONS = 2

# The five authorized execution symbols move together far more than a per-symbol
# view suggests, so same-direction exposure across them is treated as one bet.
CRYPTO_BETA_CLUSTER = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"})

LONG = "long"
SHORT = "short"


@dataclass(frozen=True, slots=True)
class RiskExposure:
    """One unit of committed or in-flight initial risk."""

    symbol: str
    direction: str
    initial_risk_usdt: Decimal
    source: str = "position"

    @property
    def normalized_direction(self) -> str:
        return LONG if str(self.direction).lower() in {LONG, "buy"} else SHORT


@dataclass(frozen=True, slots=True)
class PortfolioRiskDecision:
    """Verdict for one candidate's requested initial risk."""

    allowed: bool
    reason_code: str | None
    detail: str
    equity: Decimal
    candidate_risk_usdt: Decimal
    committed_total_risk_usdt: Decimal
    committed_cluster_risk_usdt: Decimal
    projected_total_risk_fraction: Decimal
    projected_cluster_risk_fraction: Decimal
    open_position_count: int

    @property
    def blocked(self) -> bool:
        return not self.allowed


def portfolio_risk_blocks(decision: PortfolioRiskDecision, *, diagnostic: bool) -> bool:
    """Apply E-004 policy while keeping the physical open-position cap hard."""
    return decision.reason_code is not None and (not diagnostic or decision.reason_code == "MAX_OPEN_EXPOSURES")


def _sum_risk(exposures: Iterable[RiskExposure]) -> Decimal:
    return sum((exposure.initial_risk_usdt for exposure in exposures), Decimal("0"))


def in_cluster(symbol: str) -> bool:
    return symbol in CRYPTO_BETA_CLUSTER


def evaluate_portfolio_risk(
    *,
    equity: Decimal,
    candidate_symbol: str,
    candidate_direction: str,
    candidate_initial_risk_usdt: Decimal,
    committed: Sequence[RiskExposure],
    max_total_fraction: Decimal = MAX_TOTAL_INITIAL_RISK_FRACTION,
    max_cluster_fraction: Decimal = MAX_SAME_DIRECTION_CLUSTER_RISK_FRACTION,
    max_open_positions: int = MAX_OPEN_POSITIONS,
) -> PortfolioRiskDecision:
    """Decide whether ``candidate_initial_risk_usdt`` of new risk may be opened.

    ``committed`` must contain both open positions and in-flight entry intents.
    Passing only positions is the concurrency bug this gate exists to prevent.
    """

    if equity <= 0:
        return PortfolioRiskDecision(
            allowed=False,
            reason_code="PORTFOLIO_EQUITY_UNAVAILABLE",
            detail="account equity is not positive; new exposure is fail-closed",
            equity=equity,
            candidate_risk_usdt=candidate_initial_risk_usdt,
            committed_total_risk_usdt=Decimal("0"),
            committed_cluster_risk_usdt=Decimal("0"),
            projected_total_risk_fraction=Decimal("0"),
            projected_cluster_risk_fraction=Decimal("0"),
            open_position_count=0,
        )

    candidate_direction_normalized = LONG if str(candidate_direction).lower() in {LONG, "buy"} else SHORT
    committed_total = _sum_risk(committed)
    same_direction_cluster = [
        exposure
        for exposure in committed
        if in_cluster(exposure.symbol) and exposure.normalized_direction == candidate_direction_normalized
    ]
    committed_cluster = _sum_risk(same_direction_cluster)

    projected_total = committed_total + candidate_initial_risk_usdt
    projected_cluster = committed_cluster + (
        candidate_initial_risk_usdt if in_cluster(candidate_symbol) else Decimal("0")
    )
    projected_total_fraction = projected_total / equity
    projected_cluster_fraction = projected_cluster / equity
    open_positions = [exposure for exposure in committed if exposure.source == "position"]

    def verdict(reason_code: str | None, detail: str) -> PortfolioRiskDecision:
        return PortfolioRiskDecision(
            allowed=reason_code is None,
            reason_code=reason_code,
            detail=detail,
            equity=equity,
            candidate_risk_usdt=candidate_initial_risk_usdt,
            committed_total_risk_usdt=committed_total,
            committed_cluster_risk_usdt=committed_cluster,
            projected_total_risk_fraction=projected_total_fraction,
            projected_cluster_risk_fraction=projected_cluster_fraction,
            open_position_count=len(open_positions),
        )

    if candidate_initial_risk_usdt <= 0:
        return verdict(
            "PORTFOLIO_RISK_UNMEASURABLE",
            "candidate has no measurable initial stop risk; new exposure is fail-closed",
        )
    if len(open_positions) >= max_open_positions:
        return verdict(
            "MAX_OPEN_EXPOSURES",
            f"{len(open_positions)} open positions already at the {max_open_positions} limit",
        )
    if projected_total_fraction > max_total_fraction:
        return verdict(
            "PORTFOLIO_TOTAL_RISK_LIMIT",
            f"projected total initial risk {projected_total_fraction:.6f} exceeds {max_total_fraction}",
        )
    if projected_cluster_fraction > max_cluster_fraction:
        return verdict(
            "CRYPTO_CLUSTER_RISK_LIMIT",
            (
                f"projected same-direction crypto cluster risk {projected_cluster_fraction:.6f} "
                f"exceeds {max_cluster_fraction}"
            ),
        )
    return verdict(None, "within portfolio and cluster initial-risk budget")
