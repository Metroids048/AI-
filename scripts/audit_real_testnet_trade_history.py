"""Export and reconcile all currently retrievable BTC/ETH Binance Testnet facts.

This command is read-only: it makes signed account reads and SELECTs only.  It
never submits, cancels, changes leverage, or writes to the V2 database.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from services.automated_trading.audit.trading_audit import (
    add_excursions,
    build_trade_episodes,
    canonical_symbol,
    evaluate_closed_episodes,
    event_time,
    grouped_losses,
)
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionIncident,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
    V2ReconciliationSnapshot,
)
from services.data.repository import DataRepository

DEFAULT_SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT")
WINDOW_DAYS = 7


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, Decimal)):
        return str(value.isoformat() if isinstance(value, datetime) else value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, default=_json_default, sort_keys=True))
            handle.write("\n")


def _write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _market_id(symbol: str) -> str:
    return canonical_symbol(symbol).replace("/", "")


def _call(client: Any, method_name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    method = getattr(client, method_name, None)
    if not callable(method):
        raise RuntimeError(f"CCXT client has no {method_name}")
    result = method(params)
    if not isinstance(result, list):
        raise RuntimeError(f"{method_name} returned {type(result).__name__}, expected list")
    return [dict(item) for item in result if isinstance(item, Mapping)]


def _fetch_user_trades(client: Any, symbol: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page from the oldest currently retained trade id, with a time fallback."""
    market_id = _market_id(symbol)
    rows: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {"symbol": canonical_symbol(symbol), "method": "fromId"}
    try:
        cursor = 0
        while True:
            page = _call(client, "fapiPrivateGetUserTrades", {"symbol": market_id, "fromId": cursor, "limit": 1000})
            if not page:
                break
            rows.extend(page)
            ids = [int(str(item["id"])) for item in page if str(item.get("id", "")).isdigit()]
            if not ids or len(page) < 1000:
                break
            next_cursor = max(ids) + 1
            if next_cursor <= cursor:
                raise RuntimeError("userTrades pagination cursor did not advance")
            cursor = next_cursor
    except Exception as exc:  # noqa: BLE001 - fallback preserves evidence of the API behaviour
        metadata["from_id_error"] = str(exc)
        metadata["method"] = "seven_day_windows"
        rows = []
        now = datetime.now(UTC)
        # Binance documents a finite user-trade retention period.  The fallback
        # therefore starts at that queryable boundary instead of issuing years
        # of empty account reads when ``fromId`` is unavailable.
        start = now - timedelta(days=180)
        while start <= now:
            end = min(start + timedelta(days=WINDOW_DAYS) - timedelta(milliseconds=1), now)
            page = _call(
                client,
                "fapiPrivateGetUserTrades",
                {"symbol": market_id, "startTime": int(start.timestamp() * 1000), "endTime": int(end.timestamp() * 1000), "limit": 1000},
            )
            rows.extend(page)
            start = end + timedelta(milliseconds=1)
    unique = {(str(row.get("symbol")), str(row.get("id"))): row for row in rows}
    result = sorted(unique.values(), key=lambda row: (int(row.get("time", 0)), str(row.get("id", ""))))
    metadata["records"] = len(result)
    return result, metadata


def _fetch_windowed(client: Any, method: str, symbols: Iterable[str], start: datetime, *, include_symbol: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    calls = 0
    errors: list[str] = []
    scopes = tuple(symbols) if include_symbol else (None,)
    for symbol in scopes:
        cursor = start
        while cursor <= now:
            end = min(cursor + timedelta(days=WINDOW_DAYS) - timedelta(milliseconds=1), now)
            params: dict[str, Any] = {
                "startTime": int(cursor.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 1000,
            }
            if symbol is not None:
                params["symbol"] = _market_id(symbol)
            try:
                page = _call(client, method, params)
                rows.extend(page)
                calls += 1
            except Exception as exc:  # noqa: BLE001 - history retention is recorded, not concealed
                errors.append(f"{canonical_symbol(symbol) if symbol else 'account'} {cursor.date()}: {exc}")
            cursor = end + timedelta(milliseconds=1)
    def record_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
        str(row.get("symbol", "")),
        str(row.get("id", row.get("orderId", row.get("algoId", row.get("tranId", ""))))),
        str(row.get("time", row.get("updateTime", row.get("incomeTime", "")))),
        )

    unique = {record_key(row): row for row in rows}
    return list(unique.values()), {"method": method, "calls": calls, "errors": errors, "records": len(unique)}


def _local_facts(database_url: str) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]], dict[str, str], dict[str, Any]]:
    """Read the V2 fact chain as a projection; do not mutate the database."""
    engine = create_engine(database_url)
    facts: list[dict[str, Any]] = []
    local_by_trade: dict[tuple[str, str], dict[str, Any]] = {}
    exit_reason_by_order: dict[str, str] = {}
    metadata: dict[str, Any] = {}
    try:
        with Session(engine) as session:
            cycles = list(session.scalars(select(V2ExecutionCycle).where(V2ExecutionCycle.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value)))
            decisions = list(session.scalars(select(V2ExecutionDecision)))
            intents = list(session.scalars(select(V2ExecutionIntent).where(V2ExecutionIntent.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value)))
            intent_ids = {row.intent_id for row in intents}
            orders = list(session.scalars(select(V2ExchangeOrder).where(V2ExchangeOrder.intent_id.in_(intent_ids)))) if intent_ids else []
            fills = list(session.scalars(select(V2ExchangeFill).where(V2ExchangeFill.intent_id.in_(intent_ids)))) if intent_ids else []
            positions = list(session.scalars(select(V2ManagedPosition).where(V2ManagedPosition.intent_id.in_(intent_ids)))) if intent_ids else []
            protections = list(session.scalars(select(V2ProtectionRecord)))
            reconciliations = list(session.scalars(select(V2ReconciliationSnapshot).where(V2ReconciliationSnapshot.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value)))
            incidents = list(session.scalars(select(V2ExecutionIncident)))

            by_cycle = {row.cycle_id: row for row in cycles}
            by_decision = {row.decision_id: row for row in decisions}
            by_intent = {row.intent_id: row for row in intents}
            by_order_record = {row.order_record_id: row for row in orders}
            position_by_intent = {row.intent_id: row for row in positions}
            protection_by_position = {row.position_id: row for row in protections}

            for collection, name in ((cycles, "cycle"), (decisions, "decision"), (intents, "intent"), (orders, "order"), (fills, "fill"), (positions, "position"), (protections, "protection"), (reconciliations, "reconciliation"), (incidents, "incident")):
                for record in collection:
                    row = {column.name: getattr(record, column.name) for column in record.__table__.columns}
                    facts.append({"fact_type": name, **row})

            for protection in protections:
                if protection.stop_exchange_order_id:
                    exit_reason_by_order[str(protection.stop_exchange_order_id)] = "STOP"
                if protection.tp_exchange_order_id:
                    exit_reason_by_order[str(protection.tp_exchange_order_id)] = "TAKE_PROFIT"
            for fill in fills:
                intent = by_intent.get(fill.intent_id)
                order = by_order_record.get(fill.exchange_order_record_id)
                decision = by_decision.get(intent.decision_id) if intent and intent.decision_id else None
                cycle = by_cycle.get(intent.cycle_id) if intent else None
                position = position_by_intent.get(fill.intent_id)
                protection = protection_by_position.get(position.position_id) if position else None
                context = dict(decision.payload) if decision and isinstance(decision.payload, dict) else {}
                context.update(
                    {
                        "v2_intent_id": fill.intent_id,
                        "v2_cycle_id": intent.cycle_id if intent else None,
                        "strategy": intent.candidate_key if intent else None,
                        "candidate_type": intent.candidate_type if intent else None,
                        "decision_terminal": cycle.decision_terminal if cycle else None,
                        "score_bucket": context.get("score_bucket") or context.get("score") or None,
                        "regime": context.get("regime") or context.get("market_regime") or None,
                        "local_order_id": order.exchange_order_id if order else None,
                        "is_stop_order": bool(protection and protection.stop_exchange_order_id == fill.exchange_order_id),
                        "is_tp_order": bool(protection and protection.tp_exchange_order_id == fill.exchange_order_id),
                    }
                )
                local_by_trade[(canonical_symbol(fill.symbol), str(fill.trade_id))] = context
            metadata = {
                "cycles": len(cycles), "decisions": len(decisions), "intents": len(intents), "orders": len(orders),
                "fills": len(fills), "positions": len(positions), "protections": len(protections),
                "reconciliations": len(reconciliations), "incidents": len(incidents),
                "unhealthy_reconciliations": sum(row.status != "HEALTHY" for row in reconciliations),
                "open_incidents": sum(not row.resolved for row in incidents),
            }
    finally:
        engine.dispose()
    return facts, local_by_trade, exit_reason_by_order, metadata


def _attach_excursions(database_url: str, episodes: list[Any]) -> int:
    engine = create_engine(database_url)
    measured = 0
    try:
        with Session(engine) as session:
            repository = DataRepository(session)
            for episode in episodes:
                end_at = episode.exit_time or datetime.now(UTC)
                bars = repository.list_ohlcv_bars(symbol=episode.symbol, timeframe="1m", start_at=episode.entry_time, end_at=end_at)
                if bars:
                    add_excursions(episode, ({"high": bar.high, "low": bar.low} for bar in bars))
                    measured += 1
    finally:
        engine.dispose()
    return measured


def _classify_exchange_exit_reason(episodes: list[Any], orders: Iterable[Mapping[str, Any]]) -> None:
    order_types: dict[str, str] = {}
    for order in orders:
        text = str(order.get("type") or order.get("orderType") or "").upper()
        for key in ("orderId", "algoId", "actualOrderId"):
            if order.get(key) is not None:
                order_types[str(order[key])] = text
    for episode in episodes:
        if episode.exit_reason != "UNKNOWN" or not episode.fills:
            continue
        order_id = str(episode.fills[-1]["order_id"])
        text = order_types.get(order_id, "")
        if "TAKE_PROFIT" in text:
            episode.exit_reason = "TAKE_PROFIT"
        elif "STOP" in text:
            episode.exit_reason = "STOP"


def _unmatched_rows(exchange_trades: list[dict[str, Any]], local_by_trade: Mapping[tuple[str, str], Mapping[str, Any]], local_facts: list[dict[str, Any]]) -> list[dict[str, str]]:
    exchange_ids = {(canonical_symbol(row.get("symbol")), str(row.get("id"))) for row in exchange_trades}
    local_ids = set(local_by_trade)
    rows = []
    for symbol, trade_id in sorted(exchange_ids - local_ids):
        rows.append({"record_type": "exchange_fill", "source": "Binance userTrades", "key": f"{symbol}/{trade_id}", "reason": "UNMANAGED_EXTERNAL_OR_LEGACY"})
    for symbol, trade_id in sorted(local_ids - exchange_ids):
        rows.append({"record_type": "v2_fill", "source": "local V2", "key": f"{symbol}/{trade_id}", "reason": "MISSING_FROM_EXCHANGE_USER_TRADES"})
    local_order_ids = {str(row.get("exchange_order_id")) for row in local_facts if row.get("fact_type") == "order" and row.get("exchange_order_id")}
    for order_id in sorted(local_order_ids):
        if not order_id:
            continue
    return rows


def _top_groups(episodes: list[Any], field: str, *, reverse: bool) -> dict[str, Any] | None:
    rows: dict[str, Decimal] = {}
    for episode in episodes:
        if episode.status != "CLOSED":
            continue
        key = str(episode.as_row().get(field) or "UNKNOWN")
        rows[key] = rows.get(key, Decimal("0")) + episode.net_pnl
    if not rows:
        return None
    key = sorted(rows, key=lambda item: rows[item], reverse=reverse)[0]
    return {"value": key, "net_pnl": str(rows[key])}


def audit(*, database_url: str, out_dir: Path, symbols: tuple[str, ...]) -> dict[str, Any]:
    raw_dir = out_dir / "raw"
    canonical_dir = out_dir / "canonical"
    report_dir = out_dir / "reports"
    for directory in (raw_dir, canonical_dir, report_dir):
        directory.mkdir(parents=True, exist_ok=True)

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    client = adapter._ensure_gateway()  # noqa: SLF001 - read-only audit transport
    exchange_trades: list[dict[str, Any]] = []
    trade_fetches = []
    for symbol in symbols:
        rows, metadata = _fetch_user_trades(client, symbol)
        exchange_trades.extend(rows)
        trade_fetches.append(metadata)
    exchange_trades = sorted({(str(row.get("symbol")), str(row.get("id"))): row for row in exchange_trades}.values(), key=lambda row: (int(row.get("time", 0)), str(row.get("id", ""))))
    first_trade_at = event_time(exchange_trades[0]) if exchange_trades else datetime.now(UTC)
    orders, order_fetch = _fetch_windowed(client, "fapiPrivateGetAllOrders", symbols, first_trade_at, include_symbol=True)
    income, income_fetch = _fetch_windowed(client, "fapiPrivateGetIncome", symbols, first_trade_at, include_symbol=False)
    income = [row for row in income if canonical_symbol(row.get("symbol")) in {canonical_symbol(symbol) for symbol in symbols}]
    algo_orders, algo_fetch = _fetch_windowed(client, "fapiPrivateGetAllAlgoOrders", symbols, first_trade_at, include_symbol=True)

    local_facts, local_by_trade, exit_reason_by_order, local_metadata = _local_facts(database_url)
    _write_jsonl(raw_dir / "binance_user_trades.jsonl", exchange_trades)
    _write_jsonl(raw_dir / "binance_orders.jsonl", orders)
    _write_jsonl(raw_dir / "binance_income.jsonl", income)
    _write_jsonl(raw_dir / "binance_algo_orders.jsonl", algo_orders)
    _write_jsonl(raw_dir / "local_v2_facts.jsonl", local_facts)

    episodes = build_trade_episodes(exchange_trades, funding=income, local_by_trade=local_by_trade, exit_reason_by_order=exit_reason_by_order)
    _classify_exchange_exit_reason(episodes, [*orders, *algo_orders])
    mfe_coverage = _attach_excursions(database_url, episodes)
    exchange_fill_rows = []
    for trade in exchange_trades:
        key = (canonical_symbol(trade.get("symbol")), str(trade.get("id")))
        exchange_fill_rows.append({
            "symbol": key[0], "trade_id": key[1], "order_id": str(trade.get("orderId")), "side": trade.get("side"),
            "quantity": trade.get("qty"), "price": trade.get("price"), "commission": trade.get("commission"),
            "realized_pnl": trade.get("realizedPnl"), "position_side": trade.get("positionSide"),
            "exchange_event_time": event_time(trade).isoformat(), "v2_matched": key in local_by_trade,
            "strategy": local_by_trade.get(key, {}).get("strategy"),
        })
    unmatched = _unmatched_rows(exchange_trades, local_by_trade, local_facts)
    episode_rows = [episode.as_row() for episode in episodes]
    decision_rows = [row for row in local_facts if row.get("fact_type") == "decision"]
    _write_csv(canonical_dir / "exchange_fills.csv", exchange_fill_rows)
    _write_csv(canonical_dir / "trade_episodes.csv", episode_rows)
    _write_csv(canonical_dir / "strategy_decisions.csv", decision_rows)
    _write_csv(canonical_dir / "unmatched_records.csv", unmatched)

    local_ids = set(local_by_trade)
    exchange_ids = {(canonical_symbol(row.get("symbol")), str(row.get("id"))) for row in exchange_trades}
    local_match_rate = (len(local_ids & exchange_ids) / len(local_ids)) if local_ids else None
    required_fields_valid = all(all(key in trade and trade[key] not in (None, "") for key in ("id", "orderId", "qty", "price", "time", "commission")) for trade in exchange_trades)
    completeness = {
        "status": "PASS" if exchange_trades and required_fields_valid and (local_match_rate in (None, 1.0)) else "FAIL",
        "scope": [canonical_symbol(symbol) for symbol in symbols],
        "first_exchange_trade": event_time(exchange_trades[0]).isoformat() if exchange_trades else None,
        "last_exchange_trade": event_time(exchange_trades[-1]).isoformat() if exchange_trades else None,
        "exchange_trades": len(exchange_trades), "exchange_orders": len(orders), "income_records": len(income), "algo_orders": len(algo_orders),
        "v2_local_fills": len(local_ids), "v2_matched_fills": len(local_ids & exchange_ids), "v2_match_rate": local_match_rate,
        "unmatched_records": len(unmatched), "mfe_mae_measured_episodes": mfe_coverage,
        "required_exchange_fill_fields_valid": required_fields_valid,
        "history_limits": {
            "orders": "allOrders is fetched in 7-day windows; Binance retention can omit old unfilled/cancelled orders.",
            "algo_orders": "allAlgoOrders is best-effort; unavailable or retained history is recorded in source metadata.",
            "truth": "userTrades plus income are the exchange facts for fills, fees, realised PnL, and funding.",
        },
        "source_fetches": {"user_trades": trade_fetches, "orders": order_fetch, "income": income_fetch, "algo_orders": algo_fetch},
        "local": local_metadata,
    }
    v2_episodes = [
        episode
        for episode in episodes
        if episode.local_context.get("strategy")
    ]
    account_performance = evaluate_closed_episodes(episodes)
    performance = evaluate_closed_episodes(v2_episodes)
    loss_attribution = {
        "top_loss_causes": grouped_losses(v2_episodes, "exit_reason")[:5],
        "by_strategy": grouped_losses(v2_episodes, "strategy"),
        "by_symbol": grouped_losses(v2_episodes, "symbol"),
        "by_direction": grouped_losses(v2_episodes, "direction"),
        "by_regime": grouped_losses(v2_episodes, "regime"),
        "by_score_bucket": grouped_losses(v2_episodes, "score_bucket"),
        "account_all_episodes_top_loss_causes": grouped_losses(episodes, "exit_reason")[:5],
    }
    evaluation = {
        "account_actual": account_performance,
        "v2_actual": performance,
        "v2_episode_count": len(v2_episodes),
        "best": {"symbol": _top_groups(v2_episodes, "symbol", reverse=True), "direction": _top_groups(v2_episodes, "direction", reverse=True), "strategy": _top_groups(v2_episodes, "strategy", reverse=True), "regime": _top_groups(v2_episodes, "regime", reverse=True), "score_bucket": _top_groups(v2_episodes, "score_bucket", reverse=True)},
        "worst": {"symbol": _top_groups(v2_episodes, "symbol", reverse=False), "direction": _top_groups(v2_episodes, "direction", reverse=False), "strategy": _top_groups(v2_episodes, "strategy", reverse=False), "regime": _top_groups(v2_episodes, "regime", reverse=False), "score_bucket": _top_groups(v2_episodes, "score_bucket", reverse=False)},
        "sl_tp": {"stop_hit": sum(episode.exit_reason == "STOP" for episode in v2_episodes), "tp_hit": sum(episode.exit_reason == "TAKE_PROFIT" for episode in v2_episodes), "mfe_measured": sum(episode.mfe_pct is not None for episode in v2_episodes), "mae_measured": sum(episode.mae_pct is not None for episode in v2_episodes)},
        "execution": {"reconciliation_defects": local_metadata["unhealthy_reconciliations"], "open_incidents": local_metadata["open_incidents"], "slippage": "NOT_MEASURED: no immutable submission reference price in all matched facts", "latency": "NOT_MEASURED: audit does not infer local wall-clock latency"},
    }
    (report_dir / "data_completeness.json").write_text(json.dumps(completeness, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
    (report_dir / "live_strategy_evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
    (report_dir / "loss_attribution.json").write_text(json.dumps(loss_attribution, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
    markdown = "\n".join([
        "# Live Actual Strategy Evaluation", "", f"- Data completeness: **{completeness['status']}**", f"- Scope: {', '.join(completeness['scope'])}",
        f"- Exchange trades: {completeness['exchange_trades']} (account total)", f"- Closed V2 trade episodes: {performance['trades']}", f"- V2 net PnL after commission and funding: {performance['net_pnl']} USDT", f"- V2 profit factor: {performance['profit_factor']}", f"- V2 expectancy: {performance['expectancy']} USDT", "", "## Loss Attribution", "",
        *[f"- {row['cause']}: {row['trades']} episode(s), {row['net_pnl']} USDT" for row in loss_attribution['top_loss_causes']], "",
        "Exchange userTrades and income are authoritative. Unfilled/cancelled order history is best-effort because Binance may retain it for a limited period.", "",
    ])
    (report_dir / "live_strategy_evaluation.md").write_text(markdown, encoding="utf-8")
    return {"completeness": completeness, "evaluation": evaluation, "loss_attribution": loss_attribution}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("POSTGRES_URL", "sqlite:///.local_paper_console.db"))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/trading_audit"))
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    result = audit(database_url=args.database_url, out_dir=args.out_dir, symbols=tuple(args.symbols))
    print("AUDIT_ENVIRONMENT_VERIFIED")
    print(json.dumps({"data_completeness": result["completeness"]["status"], "exchange_trades": result["completeness"]["exchange_trades"], "v2_match_rate": result["completeness"]["v2_match_rate"]}, ensure_ascii=False))
    return 0 if result["completeness"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
