"""Fresh quote, spread, signal deviation and cost-adjusted RR admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from shared.models import BlockCode, PositionSide, RiskDecision, TradeAction, TradeIntent, ValidatedMarketSnapshot


@dataclass(frozen=True, slots=True)
class PreSubmitPolicy:
    max_quote_age: timedelta
    max_signal_deviation_fraction: Decimal
    max_spread_fraction: Decimal
    minimum_reward_risk: Decimal
    round_trip_cost_fraction: Decimal


def validate_pre_submit(
    *,
    intent: TradeIntent,
    snapshot: ValidatedMarketSnapshot,
    now: datetime,
    policy: PreSubmitPolicy,
) -> RiskDecision:
    if snapshot.symbol != intent.symbol:
        raise ValueError("market snapshot symbol does not match intent")
    entry_price = snapshot.ask_price if intent.position_side is PositionSide.LONG else snapshot.bid_price
    codes: list[BlockCode] = []
    if now - snapshot.received_at > policy.max_quote_age:
        codes.append(BlockCode.DATA_STALE)
    deviation = abs(entry_price - intent.signal_reference_price) / intent.signal_reference_price
    if deviation > policy.max_signal_deviation_fraction:
        codes.append(BlockCode.PRICE_DEVIATION_EXCEEDED)
    spread = (snapshot.ask_price - snapshot.bid_price) / snapshot.mark_price
    if spread > policy.max_spread_fraction:
        codes.append(BlockCode.SPREAD_EXCEEDED)

    actual_rr: Decimal | None = None
    if intent.action is TradeAction.OPEN and intent.protection.take_profit_price is not None:
        if intent.position_side is PositionSide.LONG:
            risk = entry_price - intent.protection.stop_price
            reward = intent.protection.take_profit_price - entry_price
        else:
            risk = intent.protection.stop_price - entry_price
            reward = entry_price - intent.protection.take_profit_price
        if risk <= 0 or reward <= 0:
            actual_rr = Decimal("0")
        else:
            net_reward = max(Decimal("0"), reward - entry_price * policy.round_trip_cost_fraction)
            actual_rr = net_reward / risk
        if actual_rr < policy.minimum_reward_risk:
            codes.append(BlockCode.NET_RR_INSUFFICIENT)

    return RiskDecision(
        decision_id=intent.decision_id,
        accepted=not codes,
        requested_notional=intent.target_quantity * entry_price,
        portfolio_exposure_after=Decimal("0"),
        initial_risk_fraction_after=Decimal("0"),
        actual_reward_risk=actual_rr,
        block_codes=tuple(dict.fromkeys(codes)),
    )
