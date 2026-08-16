from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import MicrostructureSnapshot, V2DecisionSnapshot


@dataclass(frozen=True)
class ReplayWindow:
    side: str
    quantity: Decimal
    reference_price: Decimal
    target_price: Decimal
    stop_price: Decimal
    best_bid: Decimal
    best_ask: Decimal
    bid_liquidity: Decimal
    ask_liquidity: Decimal
    future_touch: bool
    timeout: bool
    adverse_move: Decimal = Decimal("0")


@dataclass(frozen=True)
class ReplayResult:
    mode: str
    status: str
    fill_price: Decimal | None
    filled_quantity: Decimal
    gross_r: Decimal
    fees_r: Decimal
    slippage_r: Decimal
    net_r: Decimal
    adverse_selection_r: Decimal


def replay_candidate_windows(session: Session, *, window_minutes: int = 5) -> dict[str, Any]:
    """Replay every persisted candidate with observed book data only.

    Missing book coverage is reported as ``unobserved`` rather than filled with
    synthetic prices. This makes the command useful before readiness and safe
    to run repeatedly as natural samples accumulate.
    """

    decisions = list(
        session.scalars(select(V2DecisionSnapshot).where(V2DecisionSnapshot.symbol.in_(("BTC/USDT", "ETH/USDT"))))
    )
    counts: dict[str, int] = {"candidate_windows": 0, "covered_windows": 0, "unobserved_windows": 0}
    by_symbol: dict[str, dict[str, int]] = {}
    results: list[ReplayResult] = []
    for decision in decisions:
        payload = decision.payload if isinstance(decision.payload, dict) else {}
        candidate = payload.get("candidate") or payload.get("trade_candidate_payload")
        funnel = payload.get("decision", {}).get("funnel", {}) if isinstance(payload.get("decision"), dict) else {}
        if not candidate and not funnel.get("created_candidate") and not funnel.get("candidate_id"):
            continue
        candidate = candidate or {}
        counts["candidate_windows"] += 1
        symbol_counts = by_symbol.setdefault(decision.symbol, {"candidate_windows": 0, "covered_windows": 0})
        symbol_counts["candidate_windows"] += 1
        start = decision.decision_time - timedelta(minutes=window_minutes)
        end = decision.decision_time + timedelta(minutes=window_minutes)
        snapshot = session.scalar(
            select(MicrostructureSnapshot)
            .where(MicrostructureSnapshot.symbol == decision.symbol)
            .where(MicrostructureSnapshot.received_at >= start)
            .where(MicrostructureSnapshot.received_at <= end)
            .where(MicrostructureSnapshot.is_valid.is_(True))
            .order_by(MicrostructureSnapshot.received_at)
            .limit(1)
        )
        if snapshot is None:
            counts["unobserved_windows"] += 1
            continue
        counts["covered_windows"] += 1
        symbol_counts["covered_windows"] += 1
        side = str(candidate.get("side", "LONG")).lower()
        reference = Decimal(str(candidate.get("signal_reference_price") or snapshot.last_price))
        stop = Decimal(str(candidate.get("stop_reference_price") or reference * Decimal("0.99")))
        target = Decimal(str(candidate.get("take_profit_reference_price") or reference * Decimal("1.01")))
        qty = Decimal("1")
        bid_liquidity = Decimal(str(snapshot.bids[0][1])) if snapshot.bids else Decimal("0")
        ask_liquidity = Decimal(str(snapshot.asks[0][1])) if snapshot.asks else Decimal("0")
        results.append(
            replay_window(
                ReplayWindow(
                    side=side,
                    quantity=qty,
                    reference_price=reference,
                    target_price=target,
                    stop_price=stop,
                    best_bid=snapshot.best_bid,
                    best_ask=snapshot.best_ask,
                    bid_liquidity=bid_liquidity,
                    ask_liquidity=ask_liquidity,
                    future_touch=False,
                    timeout=True,
                )
            )
        )
    return {
        "counts": counts,
        "by_symbol": by_symbol,
        "covered_net_r": str(sum((result.net_r for result in results), Decimal("0"))),
        "statuses": {
            status: sum(1 for result in results if result.status == status)
            for status in {result.status for result in results}
        },
    }


def replay_window(
    window: ReplayWindow, *, timeout_fallback_market: bool = True, fee_bps: Decimal = Decimal("5")
) -> ReplayResult:
    is_long = window.side.lower() == "long"
    touch_price = window.best_bid if is_long else window.best_ask
    available = window.bid_liquidity if is_long else window.ask_liquidity
    if window.future_touch and available > 0:
        qty = min(window.quantity, available)
        fill = touch_price
        status = "partial" if qty < window.quantity else "filled"
        mode = "maker_limit"
    elif window.timeout and timeout_fallback_market:
        qty = window.quantity
        fill = window.best_ask if is_long else window.best_bid
        status = "fallback_market"
        mode = "market_fallback"
    else:
        return ReplayResult(
            "maker_limit",
            "missed",
            None,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
    exit_move = (window.target_price - fill) if is_long else (fill - window.target_price)
    risk = abs(window.reference_price - window.stop_price)
    gross_r = exit_move / risk if risk else Decimal("0")
    fees_r = (fill * qty * fee_bps / Decimal("10000")) / (risk * qty) if risk else Decimal("0")
    slippage_move = fill - window.reference_price if is_long else window.reference_price - fill
    slippage_r = slippage_move / risk if risk else Decimal("0")
    adverse = window.adverse_move / abs(window.reference_price - window.stop_price)
    return ReplayResult(mode, status, fill, qty, gross_r, fees_r, slippage_r, gross_r - fees_r - slippage_r, adverse)
