"""Wait for and verify a natural scheduler-generated Binance Simulation order.

This script is evidence-only: it never creates, cancels, reduces, closes or
modifies an exchange order. It accepts only orders created by the directional
runtime scheduler and excludes Testnet acceptance/manual/canary traffic.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, exchange_to_platform_symbol
from services.database import get_session_factory
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
from services.execution.gateway import BinanceUsdtPerpetualGateway, _normalize_binance_symbol
from services.strategy_library import ExecutionRepository, PaperRunRepository
from shared.config import settings

ALLOWED_VARIANTS = {"primary", "simulation_sampling_fallback"}
DISALLOWED_ORIGINS = {"testnet_acceptance", "agent_validation", "manual_canary", "manual"}


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _directional_run_ids(session) -> set[str]:  # noqa: ANN001
    return {
        run.paper_run_id or ""
        for run in PaperRunRepository(session).list_paper_runs()
        if run.paper_status == "running"
        and run.execution_profile.get("auto_paper_runtime_key") == AUTO_PAPER_TECHNICAL_KEY
        and run.execution_profile.get("execution_mode") == "binance_testnet"
    }


def _eligible_order(order, *, run_ids: set[str], since: datetime):  # noqa: ANN001, ANN201
    if order.created_at is None or _as_utc(order.created_at) < _as_utc(since):
        return False
    if order.paper_run_id not in run_ids:
        return False
    if order.symbol not in AUTO_SIMULATION_EXECUTION_SYMBOLS:
        return False
    if order.close_only_mode or not order.gateway_order_id:
        return False
    if str(order.gateway_name or "") != "binance_usdt_perpetual":
        return False
    if str(order.order_origin or "").lower() in DISALLOWED_ORIGINS:
        return False
    if order.test_run_id:
        return False
    context = order.entry_context
    if context.get("paper_order_should_trade") is not True:
        return False
    if context.get("decision_variant", "primary") not in ALLOWED_VARIANTS:
        return False
    return context.get("observation_only_mode") is not True


def _fetch_exchange_order(gateway: BinanceUsdtPerpetualGateway, *, order_id: str, symbol: str) -> dict[str, Any]:
    payload = gateway.client.fetch_order(order_id, _normalize_binance_symbol(symbol))
    return dict(payload or {})


def _v2_natural_entries(session, *, since: datetime) -> list[dict[str, Any]]:  # noqa: ANN001
    """Read V2 Active natural entry facts without treating them as legacy orders.

    V2 Active persists exchange-first entries in its own intent/order/fill tables;
    the legacy ``order_executions`` projection is not populated for this path.
    Only an explicit Testnet Canary decision with a non-promotable sampling
    candidate is eligible here.
    """
    from sqlalchemy import select

    from services.automated_trading.infrastructure.models import (
        V2ExchangeFill,
        V2ExchangeOrder,
        V2ExecutionDecision,
        V2ExecutionIntent,
    )

    rows = session.execute(
        select(V2ExecutionIntent, V2ExchangeOrder, V2ExecutionDecision, V2ExchangeFill)
        .join(V2ExchangeOrder, V2ExchangeOrder.intent_id == V2ExecutionIntent.intent_id)
        .join(V2ExecutionDecision, V2ExecutionDecision.decision_id == V2ExecutionIntent.decision_id)
        .join(V2ExchangeFill, V2ExchangeFill.intent_id == V2ExecutionIntent.intent_id)
        .where(
            V2ExecutionIntent.execution_mode == "BINANCE_TESTNET",
            V2ExecutionIntent.candidate_type == "SAMPLING",
            V2ExecutionIntent.created_at >= _as_utc(since).replace(tzinfo=None),
            V2ExchangeOrder.exchange_order_id.is_not(None),
            V2ExchangeOrder.filled_quantity > 0,
            V2ExchangeOrder.average_fill_price > 0,
            V2ExchangeFill.reduce_only.is_(False),
        )
        .order_by(V2ExecutionIntent.created_at.asc())
    ).all()
    eligible: list[dict[str, Any]] = []
    for intent, order, decision, fill in rows:
        payload = dict(decision.payload or {})
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        if payload.get("entry_authority") != "TESTNET_CANARY":
            continue
        if payload.get("execution_policy") != "AUTHORIZED_TESTNET_CANARY":
            continue
        if candidate.get("lane") != "TESTNET_SAMPLING" or candidate.get("non_promotable") is not True:
            continue
        if candidate.get("strategy_id") != "testnet_sampling_v2":
            continue
        eligible.append(
            {
                "intent": intent,
                "order": order,
                "decision": decision,
                "fill": fill,
                "payload": payload,
                "decision_variant": "primary",
            }
        )
    return eligible


def _v2_proof(
    item: dict[str, Any], exchange_order: dict[str, Any], exchange_position: dict[str, Any] | None
) -> dict[str, Any]:
    intent = item["intent"]
    order = item["order"]
    fill = item["fill"]
    payload = item["payload"]
    status = str(exchange_order.get("status") or "").lower()
    filled = float(exchange_order.get("filled") or order.filled_quantity or fill.filled_quantity or 0)
    average = float(exchange_order.get("average") or order.average_fill_price or fill.fill_price or 0)
    if status not in {"closed", "filled"} or filled <= 0 or average <= 0:
        raise RuntimeError(f"V2 exchange order {order.exchange_order_id} is not a confirmed positive fill")
    return {
        "proof_type": "real_binance_simulation_natural_v2_order",
        "verified_at": datetime.now(UTC).isoformat(),
        "intent_id": intent.intent_id,
        "cycle_id": intent.cycle_id,
        "decision_id": intent.decision_id,
        "symbol": intent.symbol,
        "direction": intent.direction,
        "order_origin": "runtime_scheduler",
        "cycle_source": "natural_testnet_sampling",
        "signal_candle_close_time": intent.decision_bar_timestamp,
        "decision_variant": item["decision_variant"],
        "candidate_id": (payload.get("candidate") or {}).get("candidate_id"),
        "primary_rejection_reason": None,
        "gateway_order_id": str(order.exchange_order_id),
        "gateway_status_local": "filled",
        "exchange_status": status,
        "exchange_filled_quantity": filled,
        "exchange_average_fill_price": average,
        "exchange_order_timestamp": exchange_order.get("timestamp"),
        "exchange_trade_id": str(fill.trade_id),
        "exchange_position_currently_open": exchange_position is not None,
        "exchange_position": exchange_position,
        "acceptance_or_manual_order": False,
    }


def _matching_position(snapshot: dict[str, Any], *, symbol: str, direction: str) -> dict[str, Any] | None:
    wanted_side = "long" if direction == "long" else "short"
    for item in snapshot.get("open_positions", []) or []:
        platform_symbol = exchange_to_platform_symbol(str(item.get("symbol") or ""))
        side = str(item.get("side") or "").lower()
        if platform_symbol == symbol and side == wanted_side and abs(float(item.get("contracts") or 0)) > 0:
            return dict(item)
    return None


def _proof(order, exchange_order: dict[str, Any], exchange_position: dict[str, Any] | None) -> dict[str, Any]:  # noqa: ANN001
    status = str(exchange_order.get("status") or "").lower()
    filled = float(exchange_order.get("filled") or exchange_order.get("amount") or 0)
    average = float(exchange_order.get("average") or exchange_order.get("price") or 0)
    if status not in {"closed", "filled"} or filled <= 0 or average <= 0:
        raise RuntimeError(
            f"exchange order {order.gateway_order_id} is not a confirmed positive fill: "
            f"status={status!r}, filled={filled}, average={average}"
        )
    return {
        "proof_type": "real_binance_simulation_natural_directional_order",
        "verified_at": datetime.now(UTC).isoformat(),
        "order_execution_id": order.order_execution_id,
        "paper_run_id": order.paper_run_id,
        "symbol": order.symbol,
        "direction": order.direction.value,
        "order_origin": order.order_origin,
        "cycle_source": order.cycle_source,
        "scheduler_instance_id": order.scheduler_instance_id,
        "signal_candle_close_time": order.signal_candle_close_time,
        "decision_variant": order.entry_context.get("decision_variant", "primary"),
        "candidate_id": order.entry_context.get("candidate_id"),
        "primary_rejection_reason": order.entry_context.get("primary_rejection_reason"),
        "gateway_order_id": str(order.gateway_order_id),
        "gateway_status_local": order.gateway_status,
        "exchange_status": status,
        "exchange_filled_quantity": filled,
        "exchange_average_fill_price": average,
        "exchange_order_timestamp": exchange_order.get("timestamp"),
        "exchange_position_currently_open": exchange_position is not None,
        "exchange_position": exchange_position,
        "local_position_record_id": order.position_record_id,
        "acceptance_or_manual_order": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="sqlite:///.local_paper_console.db")
    parser.add_argument("--since", default=None, help="ISO timestamp; defaults to script start")
    parser.add_argument("--lookback-minutes", type=int, default=5)
    parser.add_argument("--wait-seconds", type=int, default=86400)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--output", default="artifacts/real-binance-natural-order-proof.json")
    args = parser.parse_args()

    if not settings.binance_use_testnet or settings.live_trading_enabled:
        raise SystemExit("Refusing proof outside Binance Testnet/Simulation mode")
    if not settings.binance_auto_execute:
        raise SystemExit("BINANCE_AUTO_EXECUTE must be true")
    if not settings.binance_api_key or not settings.binance_api_secret:
        raise SystemExit("Binance Simulation credentials are not configured")

    since = _parse_datetime(args.since) if args.since else datetime.now(UTC) - timedelta(minutes=args.lookback_minutes)
    deadline = time.monotonic() + max(0, args.wait_seconds)
    gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
    session = get_session_factory(args.database_url)()
    try:
        while True:
            for item in _v2_natural_entries(session, since=since):
                order = item["order"]
                exchange_order = _fetch_exchange_order(
                    gateway,
                    order_id=str(order.exchange_order_id),
                    symbol=item["intent"].symbol,
                )
                snapshot = gateway.reconcile(live_run_id=f"proof:v2:{item['intent'].cycle_id}")
                position = _matching_position(
                    snapshot,
                    symbol=item["intent"].symbol,
                    direction=str(item["intent"].direction).lower(),
                )
                try:
                    proof = _v2_proof(item, exchange_order, position)
                except RuntimeError:
                    continue
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(proof, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                print(
                    json.dumps(
                        {"status": "PASS", "proof": str(output), **proof}, ensure_ascii=False, indent=2, default=str
                    )
                )
                return 0

            run_ids = _directional_run_ids(session)
            session.expire_all()
            orders = [
                order
                for order in ExecutionRepository(session).list_orders()
                if _eligible_order(order, run_ids=run_ids, since=since)
            ]
            orders.sort(key=lambda order: order.created_at or datetime.min.replace(tzinfo=UTC))
            for order in orders:
                exchange_order = _fetch_exchange_order(
                    gateway,
                    order_id=str(order.gateway_order_id),
                    symbol=order.symbol,
                )
                snapshot = gateway.reconcile(live_run_id=f"proof:{order.paper_run_id}")
                position = _matching_position(
                    snapshot,
                    symbol=order.symbol,
                    direction=order.direction.value,
                )
                try:
                    proof = _proof(order, exchange_order, position)
                except RuntimeError:
                    continue
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(proof, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                print(
                    json.dumps(
                        {"status": "PASS", "proof": str(output), **proof}, ensure_ascii=False, indent=2, default=str
                    )
                )
                return 0
            if time.monotonic() >= deadline:
                raise SystemExit(
                    "No natural directional Binance Simulation fill was verified in the requested window. "
                    "No acceptance/manual order was created by this proof script."
                )
            time.sleep(max(1.0, args.poll_seconds))
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
