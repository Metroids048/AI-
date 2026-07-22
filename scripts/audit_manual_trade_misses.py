"""Read-only Review-layer reconstruction of manually entered trade opportunities.

The audit opens SQLite with ``mode=ro`` and never imports an execution gateway.
Decision evidence is restricted to snapshots at or before the manual entry;
post-entry OHLCV is used only for explicitly separated MFE/MAE evaluation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ManualTradeMiss:
    order_execution_id: str
    gateway_order_id: str | None
    symbol: str
    side: str
    entry_time_utc: datetime
    entry_price: float | None
    quantity: float | None
    gateway_status: str | None
    decision_time_utc: datetime | None
    decision_age_minutes: float | None
    pipeline_status: str | None
    final_blocker: str | None
    classification: str
    market_regime: str | None
    mtf_status: str | None
    signals: tuple[dict[str, Any], ...]
    ensemble: dict[str, Any] | None
    veto_result: dict[str, Any] | None
    theoretical_stop_price: float | None
    theoretical_takeprofit_price: float | None
    mfe_fraction: float | None
    mae_fraction: float | None
    reached_1r: bool | None
    reached_2r: bool | None
    evidence_gaps: tuple[str, ...]


@dataclass(frozen=True)
class ManualTradeMissReport:
    generated_at: datetime
    database: str
    market_database: str
    decision_lookback_minutes: float
    outcome_window_hours: float
    trades: tuple[ManualTradeMiss, ...]


def _as_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _final_blocker(status: str | None, action: str | None) -> str | None:
    if status in {"universe_status_rejected", "technical_signals_insufficient"}:
        return "NO_BASE_SIGNAL"
    if status in {"multi_timeframe_disagreement", "confirmation_unavailable_fail_closed"}:
        return "MTF_DISAGREEMENT"
    if status == "ensemble_discarded":
        return "ENSEMBLE_DISCARD"
    if status == "meta_label_bet_skipped":
        return "META_LABEL_SKIP"
    if status == "vetoed":
        return "LLM_VETO"
    if status == "bet_taken" and action == "rejected":
        return "RISK_BLOCK"
    if status == "bet_taken" and action and not action.startswith("open_"):
        return "POST_PIPELINE_NO_INTENT"
    return None


def _classification(
    *,
    blocker: str | None,
    side: str,
    signals: tuple[dict[str, Any], ...],
    entry_price: float | None,
    mfe_fraction: float | None,
    mae_fraction: float | None,
    reached_1r: bool | None,
) -> str:
    if entry_price is None:
        return "INSUFFICIENT_EVIDENCE"
    signal_sides = {str(signal.get("side") or "").lower() for signal in signals}
    if blocker == "NO_BASE_SIGNAL":
        return "NO_BASE_SIGNAL"
    outcome_supports_false_negative = reached_1r is True or (
        reached_1r is None and mfe_fraction is not None and mae_fraction is not None and mfe_fraction > mae_fraction
    )
    if blocker == "ENSEMBLE_DISCARD" and side in signal_sides:
        return "ENSEMBLE_FALSE_NEGATIVE" if outcome_supports_false_negative else "INSUFFICIENT_EVIDENCE"
    if blocker == "LLM_VETO" and side in signal_sides:
        return "LLM_FALSE_VETO" if outcome_supports_false_negative else "INSUFFICIENT_EVIDENCE"
    if blocker == "MTF_DISAGREEMENT" and side in signal_sides:
        return "MTF_OVER_FILTER" if outcome_supports_false_negative else "INSUFFICIENT_EVIDENCE"
    if blocker in {"RISK_BLOCK", "POST_PIPELINE_NO_INTENT"}:
        return "VALID_FILTER"
    return "INSUFFICIENT_EVIDENCE" if blocker is None else "VALID_FILTER"


def _nearest_prior_decision(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    entry_time: datetime,
    lookback: timedelta,
) -> sqlite3.Row | None:
    rows = connection.execute(
        "SELECT action, pipeline_status, reason, decision_trace, cycle_time "
        "FROM decision_snapshots WHERE symbol = ? ORDER BY cycle_time DESC",
        (symbol,),
    ).fetchall()
    earliest = entry_time - lookback
    for row in rows:
        cycle_time = _as_utc(row[4])
        if earliest <= cycle_time <= entry_time:
            return row
    return None


def _outcome_excursions(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    entry_time: datetime,
    entry_price: float | None,
    outcome_window: timedelta,
) -> tuple[float | None, float | None, int]:
    if entry_price is None:
        return None, None, 0
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ohlcv_bars' LIMIT 1"
        ).fetchone()
        is None
    ):
        return None, None, 0
    rows = connection.execute(
        "SELECT time, high, low FROM ohlcv_bars WHERE symbol = ? AND timeframe = '15m' ORDER BY time",
        (symbol,),
    ).fetchall()
    end_time = entry_time + outcome_window
    bars = [row for row in rows if entry_time <= _as_utc(row[0]) <= end_time]
    if not bars:
        return None, None, 0
    highest = max(float(row[1]) for row in bars)
    lowest = min(float(row[2]) for row in bars)
    if side == "short":
        return (entry_price - lowest) / entry_price, (highest - entry_price) / entry_price, len(bars)
    return (highest - entry_price) / entry_price, (entry_price - lowest) / entry_price, len(bars)


def _theoretical_levels(trace: dict[str, Any]) -> tuple[float | None, float | None]:
    stop = _positive_float(trace.get("theoretical_stop_price"))
    takeprofit = _positive_float(trace.get("theoretical_takeprofit_price"))
    signals = trace.get("signals")
    if isinstance(signals, list) and signals:
        first = signals[0] if isinstance(signals[0], dict) else {}
        stop = stop or _positive_float(first.get("stoploss"))
        takeprofits = first.get("takeprofits")
        if takeprofit is None and isinstance(takeprofits, list) and takeprofits:
            takeprofit = _positive_float(takeprofits[0])
    return stop, takeprofit


def analyze_manual_trades(
    database: Path,
    *,
    market_database: Path | None = None,
    order_execution_ids: tuple[str, ...],
    decision_lookback: timedelta = timedelta(minutes=90),
    outcome_window: timedelta = timedelta(hours=24),
) -> ManualTradeMissReport:
    resolved = database.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    resolved_market = market_database.expanduser().resolve() if market_database is not None else resolved
    if not resolved_market.is_file():
        raise FileNotFoundError(resolved_market)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    market_connection = connection
    if resolved_market != resolved:
        market_connection = sqlite3.connect(f"file:{resolved_market.as_posix()}?mode=ro", uri=True)
        market_connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT order_execution_id, symbol, direction, close_only_mode, gateway_order_id, "
            "gateway_status, entry_context, created_at FROM order_executions"
        ).fetchall()
        by_id = {str(row[0]): row for row in rows}
        missing = [order_id for order_id in order_execution_ids if order_id not in by_id]
        if missing:
            raise ValueError(f"order execution ids not found: {', '.join(missing)}")
        trades: list[ManualTradeMiss] = []
        for order_id in order_execution_ids:
            row = by_id[order_id]
            if bool(row[3]):
                raise ValueError(f"manual trade candidate {order_id} is close-only")
            symbol = str(row[1])
            side = str(row[2]).lower()
            entry_time = _as_utc(row[7])
            context = _json_object(row[6])
            entry_price = _positive_float(context.get("actual_avg_price"))
            quantity = _positive_float(context.get("quantity"))
            decision = _nearest_prior_decision(
                connection,
                symbol=symbol,
                entry_time=entry_time,
                lookback=decision_lookback,
            )
            action = str(decision[0]) if decision is not None else None
            status = str(decision[1]) if decision is not None and decision[1] is not None else None
            trace = _json_object(decision[3]) if decision is not None else {}
            decision_time = _as_utc(decision[4]) if decision is not None else None
            raw_signals = trace.get("signals")
            signals = (
                tuple(item for item in raw_signals if isinstance(item, dict)) if isinstance(raw_signals, list) else ()
            )
            blocker = _final_blocker(status, action)
            volatility = trace.get("volatility") if isinstance(trace.get("volatility"), dict) else {}
            mtf = volatility.get("multi_timeframe") if isinstance(volatility.get("multi_timeframe"), dict) else {}
            ensemble = trace.get("ensemble") if isinstance(trace.get("ensemble"), dict) else None
            veto_result = trace.get("veto_result") if isinstance(trace.get("veto_result"), dict) else None
            stop_price, takeprofit_price = _theoretical_levels(trace)
            mfe, mae, outcome_bar_count = _outcome_excursions(
                market_connection,
                symbol=symbol,
                side=side,
                entry_time=entry_time,
                entry_price=entry_price,
                outcome_window=outcome_window,
            )
            risk_fraction = (
                abs(entry_price - stop_price) / entry_price
                if entry_price is not None and stop_price is not None
                else None
            )
            reached_1r = mfe >= risk_fraction if mfe is not None and risk_fraction else None
            reached_2r = mfe >= 2 * risk_fraction if mfe is not None and risk_fraction else None
            gaps: list[str] = []
            if entry_price is None:
                gaps.append("entry fill price is unavailable")
            if decision is None:
                gaps.append("no same-symbol decision snapshot exists inside the pre-entry lookback")
            if stop_price is None or takeprofit_price is None:
                gaps.append("persisted theoretical stop/take-profit is unavailable")
            if entry_price is not None and outcome_bar_count == 0:
                gaps.append("post-entry 15m OHLCV is unavailable")
            trades.append(
                ManualTradeMiss(
                    order_execution_id=order_id,
                    gateway_order_id=str(row[4]) if row[4] is not None else None,
                    symbol=symbol,
                    side=side,
                    entry_time_utc=entry_time,
                    entry_price=entry_price,
                    quantity=quantity,
                    gateway_status=str(row[5]) if row[5] is not None else None,
                    decision_time_utc=decision_time,
                    decision_age_minutes=(entry_time - decision_time).total_seconds() / 60 if decision_time else None,
                    pipeline_status=status,
                    final_blocker=blocker,
                    classification=_classification(
                        blocker=blocker,
                        side=side,
                        signals=signals,
                        entry_price=entry_price,
                        mfe_fraction=mfe,
                        mae_fraction=mae,
                        reached_1r=reached_1r,
                    ),
                    market_regime=str(volatility.get("regime")) if volatility.get("regime") else None,
                    mtf_status=str(mtf.get("status")) if mtf.get("status") else None,
                    signals=signals,
                    ensemble=ensemble,
                    veto_result=veto_result,
                    theoretical_stop_price=stop_price,
                    theoretical_takeprofit_price=takeprofit_price,
                    mfe_fraction=mfe,
                    mae_fraction=mae,
                    reached_1r=reached_1r,
                    reached_2r=reached_2r,
                    evidence_gaps=tuple(gaps),
                )
            )
        return ManualTradeMissReport(
            generated_at=datetime.now(UTC),
            database=resolved.as_posix(),
            market_database=resolved_market.as_posix(),
            decision_lookback_minutes=decision_lookback.total_seconds() / 60,
            outcome_window_hours=outcome_window.total_seconds() / 3600,
            trades=tuple(trades),
        )
    finally:
        if market_connection is not connection:
            market_connection.close()
        connection.close()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _format_optional(value: Any) -> str:
    return "" if value is None else str(value)


def _markdown(report: ManualTradeMissReport) -> str:
    lines = [
        "# Manual Trade Miss Analysis",
        "",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Database: {report.database}",
        f"- Market database: {report.market_database}",
        f"- Decision lookback: {report.decision_lookback_minutes:.0f} minutes",
        f"- Outcome window: {report.outcome_window_hours:.0f} hours",
        "- Decision reconstruction uses only snapshots at or before entry; "
        "MFE/MAE uses later bars and is outcome evidence only.",
    ]
    for trade in report.trades:
        lines.extend(
            [
                "",
                f"## {trade.symbol} {trade.order_execution_id}",
                "",
                f"- Side: {trade.side}",
                f"- Entry time (UTC): {trade.entry_time_utc.isoformat()}",
                f"- Entry price: {_format_optional(trade.entry_price)}",
                f"- Quantity: {_format_optional(trade.quantity)}",
                f"- Gateway status: {_format_optional(trade.gateway_status)}",
                f"- Prior decision time (UTC): "
                f"{trade.decision_time_utc.isoformat() if trade.decision_time_utc else ''}",
                f"- Decision age (minutes): {_format_optional(trade.decision_age_minutes)}",
                f"- Pipeline status: {_format_optional(trade.pipeline_status)}",
                f"- Final blocker: {_format_optional(trade.final_blocker)}",
                f"- Classification: {trade.classification}",
                f"- Market regime: {_format_optional(trade.market_regime)}",
                f"- MTF status: {_format_optional(trade.mtf_status)}",
                f"- Theoretical stop: {_format_optional(trade.theoretical_stop_price)}",
                f"- Theoretical take-profit: {_format_optional(trade.theoretical_takeprofit_price)}",
                f"- MFE fraction: {_format_optional(trade.mfe_fraction)}",
                f"- MAE fraction: {_format_optional(trade.mae_fraction)}",
                f"- Reached 1R: {_format_optional(trade.reached_1r)}",
                f"- Reached 2R: {_format_optional(trade.reached_2r)}",
                f"- Signals: `{_json_text(trade.signals)}`",
                f"- Ensemble: `{_json_text(trade.ensemble)}`",
                f"- LLM veto: `{_json_text(trade.veto_result)}`",
            ]
        )
        if trade.evidence_gaps:
            lines.extend(["", "Evidence gaps:"])
            lines.extend(f"- {gap}" for gap in trade.evidence_gaps)
    return "\n".join(lines) + "\n"


def write_artifacts(
    report: ManualTradeMissReport,
    *,
    csv_path: Path,
    markdown_path: Path,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "order_execution_id",
        "symbol",
        "side",
        "entry_time_utc",
        "entry_price",
        "quantity",
        "gateway_order_id",
        "gateway_status",
        "decision_time_utc",
        "decision_age_minutes",
        "pipeline_status",
        "final_blocker",
        "classification",
        "market_regime",
        "mtf_status",
        "theoretical_stop_price",
        "theoretical_takeprofit_price",
        "mfe_fraction",
        "mae_fraction",
        "reached_1r",
        "reached_2r",
        "signals",
        "ensemble",
        "veto_result",
        "evidence_gaps",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trade in report.trades:
            payload = asdict(trade)
            payload["entry_time_utc"] = trade.entry_time_utc.isoformat()
            payload["decision_time_utc"] = trade.decision_time_utc.isoformat() if trade.decision_time_utc else ""
            payload["signals"] = _json_text(trade.signals)
            payload["ensemble"] = _json_text(trade.ensemble)
            payload["veto_result"] = _json_text(trade.veto_result)
            payload["evidence_gaps"] = _json_text(trade.evidence_gaps)
            writer.writerow(payload)
    markdown_path.write_text(_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local_runtime_ledger.db"))
    parser.add_argument("--market-database", type=Path, default=None)
    parser.add_argument("--order-execution-id", action="append", required=True)
    parser.add_argument("--decision-lookback-minutes", type=float, default=90.0)
    parser.add_argument("--outcome-window-hours", type=float, default=24.0)
    parser.add_argument("--csv-path", type=Path, default=Path("artifacts/manual-trade-miss-analysis.csv"))
    parser.add_argument("--report-path", type=Path, default=Path("docs/audit/manual-trade-miss-analysis.md"))
    args = parser.parse_args()
    report = analyze_manual_trades(
        args.database,
        market_database=args.market_database,
        order_execution_ids=tuple(args.order_execution_id),
        decision_lookback=timedelta(minutes=args.decision_lookback_minutes),
        outcome_window=timedelta(hours=args.outcome_window_hours),
    )
    write_artifacts(report, csv_path=args.csv_path, markdown_path=args.report_path)
    print(
        json.dumps(
            {
                "generated_at": report.generated_at.isoformat(),
                "trade_count": len(report.trades),
                "classifications": [trade.classification for trade in report.trades],
                "csv_path": args.csv_path.as_posix(),
                "report_path": args.report_path.as_posix(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
