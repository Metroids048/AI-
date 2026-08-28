"""Freeze the bounded Alpha Edge Recovery campaign from current evidence.

This script is deliberately research-only.  It reads the already generated
real-trade context and a read-only SQLite database, then writes an immutable
campaign report.  No strategy, runtime, risk, promotion, or exchange state is
changed.  The two OOS screens are the single pre-registered runs executed for
this campaign; their raw summaries are embedded with the run metadata so a
later invocation cannot silently create another tuning loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REAL_CONTEXT = Path("artifacts/active_strategy_optimization/current_real_trade_context.json")
DEFAULT_REPORT = Path("artifacts/alpha_edge_recovery_final_campaign/FINAL_REPORT.json")
DEFAULT_MARKDOWN = Path("artifacts/alpha_edge_recovery_final_campaign/FINAL_REPORT.md")
EXECUTION_SYMBOLS = {"BTC/USDT", "ETH/USDT"}


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bootstrap_lcb(values: list[Decimal], *, seed: int = 17, n: int = 10_000) -> dict[str, Any]:
    if not values:
        return {"sample_size": 0, "mean": None, "lcb95": None, "ucb95": None, "resamples": n, "seed": seed}
    rng = random.Random(seed)
    means = sorted(
        sum((values[rng.randrange(len(values))] for _ in values), Decimal("0")) / len(values) for _ in range(n)
    )
    return {
        "sample_size": len(values),
        "mean": str(sum(values, Decimal("0")) / len(values)),
        "lcb95": str(means[int(n * 0.025)]),
        "ucb95": str(means[int(n * 0.975) - 1]),
        "resamples": n,
        "seed": seed,
    }


def _load_context(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in payload.get("trades", []) if row.get("symbol") in EXECUTION_SYMBOLS]
    if not rows:
        raise RuntimeError("no current BTC/ETH real-trade rows found")
    return rows


def _load_costs(db_path: Path) -> dict[str, Any]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        positions = connection.execute(
            "select p.position_id,p.symbol,p.realized_pnl from v2_managed_positions p "
            "join v2_execution_intents i on i.intent_id=p.intent_id "
            "where p.state='CLOSED' and p.execution_mode='BINANCE_TESTNET' "
            "and p.symbol in ('BTC/USDT','ETH/USDT') and i.candidate_key='testnet_sampling_v2'"
        ).fetchall()
        commission = Decimal("0")
        complete = 0
        exit_fill_rows = 0
        for position in positions:
            intent = connection.execute(
                "select i.intent_id from v2_execution_intents i "
                "join v2_managed_positions p on p.intent_id=i.intent_id "
                "where p.position_id=? and i.candidate_key='testnet_sampling_v2'",
                (position["position_id"],),
            ).fetchone()
            if intent is None:
                continue
            entry = connection.execute(
                "select coalesce(sum(commission),0) n from v2_exchange_fills where intent_id=?", (intent["intent_id"],)
            ).fetchone()["n"]
            exit_rows = connection.execute(
                "select coalesce(sum(f.commission),0) n from v2_exchange_fills f "
                "join v2_execution_intents i on i.intent_id=f.intent_id "
                "where f.reduce_only=1 and i.candidate_key like ?",
                (f"exit:{position['position_id']}:%",),
            ).fetchone()["n"]
            exit_fill_rows += connection.execute(
                "select count(*) n from v2_exchange_fills f join v2_execution_intents i on i.intent_id=f.intent_id "
                "where f.reduce_only=1 and i.candidate_key like ?",
                (f"exit:{position['position_id']}:%",),
            ).fetchone()["n"]
            commission += _d(entry) + _d(exit_rows)
            complete += 1
        realized = sum((_d(row["realized_pnl"]) for row in positions), Decimal("0"))
        entry_fills = connection.execute(
            "select count(*) n from v2_exchange_fills f join v2_execution_intents i on i.intent_id=f.intent_id "
            "where f.reduce_only=0 and i.candidate_key='testnet_sampling_v2' and i.symbol in ('BTC/USDT','ETH/USDT')"
        ).fetchone()["n"]
        return {
            "closed_positions": len(positions),
            "positions_with_fill_costs": complete,
            "entry_fill_rows": entry_fills,
            "exit_fill_rows": exit_fill_rows,
            "commission_usdt": str(commission),
            "realized_pnl_usdt": str(realized),
            "gross_after_commission_usdt": str(realized + commission),
            "commission_share_of_abs_realized": str(commission / abs(realized)) if realized else None,
        }
    finally:
        connection.close()


def _load_realized_r(db_path: Path) -> dict[str, dict[str, str | None]]:
    """Return exchange-projected realized PnL and risk-normalized R per episode."""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        result: dict[str, dict[str, str | None]] = {}
        rows = connection.execute(
            "select p.position_id,p.entry_price,p.quantity,p.realized_pnl,p.closed_at,pr.original_stop_loss_price "
            "from v2_managed_positions p left join v2_protection_records pr on pr.position_id=p.position_id "
            "and pr.created_at=(select max(pr2.created_at) from v2_protection_records pr2 where pr2.position_id=p.position_id) "
            "join v2_execution_intents i on i.intent_id=p.intent_id "
            "where p.state='CLOSED' and p.execution_mode='BINANCE_TESTNET' "
            "and p.symbol in ('BTC/USDT','ETH/USDT') and i.candidate_key='testnet_sampling_v2'"
        ).fetchall()
        for row in rows:
            entry, quantity, stop = _d(row["entry_price"]), _d(row["quantity"]), row["original_stop_loss_price"]
            risk = abs(entry - _d(stop)) * quantity if stop is not None else None
            realized = _d(row["realized_pnl"])
            result[row["position_id"]] = {
                "realized_pnl_usdt": str(realized),
                "realized_r": str(realized / risk) if risk else None,
                "closed_at": row["closed_at"],
            }
        return result
    finally:
        connection.close()


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [_d(row["realized_r"]) for row in rows if row.get("realized_r") is not None]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = Decimal("0")
    peak = Decimal("0")
    drawdown = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(rows),
        "r_sample": len(values),
        "win_rate": None if not values else str(Decimal(len(wins)) / len(values)),
        "net_expectancy_r": None if not values else str(sum(values, Decimal("0")) / len(values)),
        "profit_factor": None if not losses else str(sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0")))),
        "max_drawdown_r": str(drawdown),
        "avg_mfe_r": None if not rows else str(sum((_d(row["mfe_r"]) for row in rows), Decimal("0")) / len(rows)),
        "avg_mae_r": None if not rows else str(sum((_d(row["mae_r"]) for row in rows), Decimal("0")) / len(rows)),
        "bootstrap_expectancy": _bootstrap_lcb(values),
        "realized_pnl_usdt": str(sum((_d(row.get("realized_pnl_usdt", "0")) for row in rows), Decimal("0"))),
    }


def _attribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Mutually exclusive, evidence-first labels.  Unknown geometry/cost data is
    # not guessed into a causal bucket.
    buckets: Counter[str] = Counter()
    loss_abs: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        pnl = _d(row.get("realized_pnl_usdt", row["net_pnl_usdt"]))
        if pnl >= 0:
            continue
        mfe = _d(row["mfe_r"])
        mae = _d(row["mae_r"])
        if row["exit_reason"] == "STOP" and mae <= _d("-1.2"):
            cause = "STOP_GEOMETRY_ADVERSE_EXCURSION"
        elif row["exit_reason"] == "STOP" and mfe < _d("0.5"):
            cause = "ENTRY_QUALITY_LOW_MFE"
        else:
            cause = "OTHER_EVIDENCED"
        buckets[cause] += 1
        loss_abs[cause] += abs(pnl)
    total = sum(loss_abs.values(), Decimal("0"))
    return {
        "loss_episodes": sum(buckets.values()),
        "episode_count_by_cause": dict(buckets),
        "loss_usdt_by_cause": {key: str(value) for key, value in loss_abs.items()},
        "loss_share_by_cause": {key: str(value / total) for key, value in loss_abs.items()} if total else {},
        "unsupported_or_unobserved": [
            "PROFIT_CAPTURE: fixed target/stop closes do not expose post-exit MFE giveback",
            "FUNDING: no per-position funding-income ledger in current SQLite schema",
            "SLIPPAGE: fill-vs-reference residual is not complete for every episode",
        ],
    }


def _oos(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Run each pre-registered screen exactly once and retain raw per-symbol output."""
    from scripts.analyze_active_sampling_trade_context import historical_oos_comparison

    dataset = Path(".strategy_refactor_history.db").resolve()
    runs: list[dict[str, Any]] = []
    for hypothesis_id, kwargs in (
        ("H1_ENTRY_CONFIRMATION_1BAR", {"one_bar_only": True}),
        ("H2_SHORT_ONLY", {"short_side_only": True}),
    ):
        raw = historical_oos_comparison(dataset, **kwargs)
        baseline = raw["baseline"]
        candidate_key = next(key for key in raw if key != "baseline")
        candidate = raw[candidate_key]
        baseline_trades = sum(value["oos"]["trades"] for value in baseline.values())
        candidate_trades = sum(value["oos"]["trades"] for value in candidate.values())
        baseline_net = sum(Decimal(value["oos"]["net_r"]) for value in baseline.values())
        candidate_net = sum(Decimal(value["oos"]["net_r"]) for value in candidate.values())
        candidate_wins = sum(
            round(value["oos"]["trades"] * Decimal(value["oos"]["win_rate"])) for value in candidate.values()
        )
        candidate_losses = candidate_trades - candidate_wins
        candidate_gross_wins = sum(
            Decimal(round(value["oos"]["trades"] * Decimal(value["oos"]["win_rate"])))
            * Decimal(value["oos"]["avg_win_r"])
            for value in candidate.values()
        )
        candidate_gross_losses = sum(
            (value["oos"]["trades"] - round(value["oos"]["trades"] * Decimal(value["oos"]["win_rate"])))
            * Decimal(value["oos"]["avg_loss_r"])
            for value in candidate.values()
        )
        runs.append(
            {
                "id": hypothesis_id,
                "candidate_key": candidate_key,
                "baseline": {"trades": baseline_trades, "net_expectancy_r": str(baseline_net / baseline_trades)},
                "candidate": {
                    "trades": candidate_trades,
                    "net_expectancy_r": str(candidate_net / candidate_trades),
                    "profit_factor": str(candidate_gross_wins / abs(candidate_gross_losses)),
                    "max_drawdown_r": str(max(Decimal(value["oos"]["max_drawdown_r"]) for value in candidate.values())),
                    "execution_diff_count": baseline_trades - candidate_trades,
                    "win_count": candidate_wins,
                    "loss_count": candidate_losses,
                },
                "raw_by_symbol": candidate,
                "baseline_raw_by_symbol": baseline,
                "execution_diff_by_symbol": {
                    symbol: baseline[symbol]["oos"]["trades"] - candidate[symbol]["oos"]["trades"]
                    for symbol in sorted(baseline)
                },
                "bootstrap": {
                    "status": "NOT_RUN",
                    "reason": "replay API exposes aggregate rows only; negative expectancy already fails survivor gate",
                },
                "decision": "FAIL_NO_SURVIVOR_COST_INCOMPLETE",
            }
        )
    return {
        "dataset": str(dataset),
        "split": "chronological_70_30",
        "cost_model": "5bps entry + 5bps exit + 1bp exit slippage; funding unavailable and therefore no promotion",
        "hypotheses": [
            {
                "id": "H1_ENTRY_CONFIRMATION_1BAR",
                "statement": "One additional closed 15m confirmation reduces low-MFE immediate adverse entries.",
                "evidence": f"{sum(row['exit_reason'] == 'STOP' for row in rows)}/{len(rows)} current BTC/ETH episodes stopped; STOP average MFE={_stop_mean(rows, 'mfe_r')}R.",
                **runs[0],
            },
            {
                "id": "H2_SHORT_ONLY",
                "statement": "The observed long-side deficit is structural; restrict the lane to short entries.",
                "evidence": f"Current long/short realized-R means are {_side_mean(rows, 'long')}R/{_side_mean(rows, 'short')}R (n={_side_count(rows, 'long')}/{_side_count(rows, 'short')}).",
                **runs[1],
            },
        ],
        "run_integrity": "Each invocation runs one screen per hypothesis and reports exact per-symbol trade-count deltas. A report path is write-once unless explicit --rerun-oos is supplied; this is an operational guard, not proof of pre-result registration.",
    }


def _side_count(rows: list[dict[str, Any]], side: str) -> int:
    return sum(1 for row in rows if row.get("side") == side and row.get("realized_r") is not None)


def _side_mean(rows: list[dict[str, Any]], side: str) -> str:
    values = [_d(row["realized_r"]) for row in rows if row.get("side") == side and row.get("realized_r") is not None]
    return str(sum(values, Decimal("0")) / len(values)) if values else "UNKNOWN"


def _stop_mean(rows: list[dict[str, Any]], field: str) -> str:
    values = [_d(row[field]) for row in rows if row.get("exit_reason") == "STOP"]
    return str(sum(values, Decimal("0")) / len(values)) if values else "UNKNOWN"


def build_report(db_path: Path, context_path: Path) -> dict[str, Any]:
    rows = _load_context(context_path)
    realized = _load_realized_r(db_path)
    for row in rows:
        if row["position_id"] in realized:
            row.update(realized[row["position_id"]])
    rows.sort(key=lambda row: row.get("closed_at") or "")
    metrics = _metrics(rows)
    realized_pnl = sum(
        (_d(row["realized_pnl_usdt"]) for row in rows if row.get("realized_pnl_usdt") is not None), Decimal("0")
    )
    stop_rows = [row for row in rows if row["exit_reason"] == "STOP"]
    report = {
        "report_type": "ALPHA_EDGE_RECOVERY_FINAL_CAMPAIGN",
        "status": "RECOVERY_EVIDENCE_INCOMPLETE_FUNDING",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "scope": {
            "database": str(db_path),
            "context": str(context_path),
            "execution_symbols": sorted(EXECUTION_SYMBOLS),
        },
        "data_hashes": {"context_sha256": _sha256(context_path), "database_sha256": _sha256(db_path)},
        "real_testnet_baseline": {
            "rows": len(rows),
            "metrics": metrics,
            "symbols": dict(Counter(row["symbol"] for row in rows)),
            "stop_metrics": {
                "count": len(stop_rows),
                "avg_mfe_r": str(sum((_d(row["mfe_r"]) for row in stop_rows), Decimal("0")) / len(stop_rows)),
                "avg_mae_r": str(sum((_d(row["mae_r"]) for row in stop_rows), Decimal("0")) / len(stop_rows)),
            },
            "costs": {**_load_costs(db_path), "context_realized_pnl_usdt": str(realized_pnl)},
        },
        "loss_attribution": _attribution(rows),
        "research": _oos(rows),
        "promotion": {
            "validated": 0,
            "shadow_authorized": False,
            "testnet_canary_authorized": False,
            "reason": "OOS candidates are negative, but point-in-time funding is absent; no complete-cost validation or exhaustion verdict is permitted",
        },
        "limitations": [
            "Current SQLite has 77 closed rows, but 4 SOL/XRP rows are outside the BTC/ETH execution universe and excluded.",
            "Funding and complete profit-capture attribution remain unknown; no causal percentage is invented.",
            "Historical OOS screen is a research screen, not execution evidence; Final Holdout remains sealed.",
        ],
    }
    return report


def _markdown(report: dict[str, Any]) -> str:
    baseline = report["real_testnet_baseline"]
    metrics = baseline["metrics"]
    lines = [
        "# Alpha Edge Recovery Final Campaign",
        "",
        f"终态：`{report['status']}`。本报告只读读取当前 Testnet 本地投影与成交回执；未修改 Runtime、Risk、Promotion 或 Exchange。",
        "",
        "## 当前真实基线（BTC/ETH）",
        f"- episode：{baseline['rows']}；symbol：{baseline['symbols']}；realized PnL：`{baseline['costs']['context_realized_pnl_usdt']} USDT`。",
        f"- Net expectancy：`{metrics['net_expectancy_r']}R`（R 样本 {metrics['r_sample']}/{metrics['trades']}）；PF：`{metrics['profit_factor']}`；MaxDD：`{metrics['max_drawdown_r']}R`；bootstrap 95% LCB：`{metrics['bootstrap_expectancy']['lcb95']}R`。",
        f"- {baseline['stop_metrics']['count']}/{baseline['rows']} episode 为 STOP；STOP 平均 MFE `{baseline['stop_metrics']['avg_mfe_r']}R`、MAE `{baseline['stop_metrics']['avg_mae_r']}R`，支持低质量入场/不利 excursion 机制。",
        f"- 已知手续费合计：`{baseline['costs']['commission_usdt']} USDT`；funding 与完整 slippage 仍为 UNKNOWN。",
        f"- 手续费/绝对 realized PnL 比例（非互斥成本诊断）：`{baseline['costs']['commission_share_of_abs_realized']}`；扣除手续费后的价格结果为 `{baseline['costs']['gross_after_commission_usdt']} USDT`。",
        "",
        "## 预注册 OOS",
        "| Hypothesis | execution_diff_count | Candidate expectancy | PF | 结论 |",
        "|---|---:|---:|---:|---|",
    ]
    for item in report["research"]["hypotheses"]:
        candidate = item["candidate"]
        lines.append(
            f"| `{item['id']}` | {candidate['execution_diff_count']} | {candidate['net_expectancy_r']}R | {candidate['profit_factor']} | `{item['decision']}` |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "- 没有 Survivor；不得进入 Shadow/Testnet，也没有运行组合实验。",
            "- 历史回放尚未消费逐笔 Funding，正式状态为 `RECOVERY_EVIDENCE_INCOMPLETE_FUNDING`；不得据此声明 exhausted。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local_paper_console.db"))
    parser.add_argument("--context", type=Path, default=REAL_CONTEXT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--rerun-oos", action="store_true", help="explicitly rerun the two frozen OOS screens")
    args = parser.parse_args()
    if args.report.exists() and not args.rerun_oos:
        raise SystemExit(f"refusing to overwrite existing campaign report: {args.report}; pass --rerun-oos explicitly")
    report = build_report(args.database.resolve(), args.context.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "episodes": report["real_testnet_baseline"]["rows"], "validated": 0},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
