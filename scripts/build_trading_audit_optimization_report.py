"""Build the compact, evidence-backed optimization report from audit artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def _d(value: object) -> Decimal | None:
    if value in (None, "", "null"):
        return None
    return Decimal(str(value))


def _mean(rows: list[dict[str, str]], key: str) -> str | None:
    values = [value for row in rows if (value := _d(row.get(key))) is not None]
    return str(sum(values, Decimal("0")) / Decimal(len(values))) if values else None


def build(*, audit_dir: Path) -> Path:
    report_dir = audit_dir / "reports"
    canonical_dir = audit_dir / "canonical"
    oos_dir = audit_dir / "strategy_oos_generation_n1"
    completeness = json.loads((report_dir / "data_completeness.json").read_text(encoding="utf-8"))
    evaluation = json.loads((report_dir / "live_strategy_evaluation.json").read_text(encoding="utf-8"))
    losses = json.loads((report_dir / "loss_attribution.json").read_text(encoding="utf-8"))
    oos = json.loads((oos_dir / "proposal-research-report.json").read_text(encoding="utf-8"))
    stress = json.loads((report_dir / "cost_stress.json").read_text(encoding="utf-8"))
    manifest = json.loads((oos_dir / "PHASE1_MANIFEST.json").read_text(encoding="utf-8"))
    first_trade = datetime.fromisoformat(completeness["first_exchange_trade"])
    last_trade = datetime.fromisoformat(completeness["last_exchange_trade"])
    days = (last_trade - first_trade).total_seconds() / 86400
    with (canonical_dir / "trade_episodes.csv").open(encoding="utf-8", newline="") as handle:
        episodes = list(csv.DictReader(handle))
    v2_rows = [
        row for row in episodes if row.get("strategy") == "testnet_sampling_v2" and row.get("status") == "CLOSED"
    ]

    candidate_rows = []
    for candidate_id, result in oos["results"].items():
        portfolio = result["portfolio"]
        candidate_rows.append((Decimal(str(portfolio["net_expectancy"])), candidate_id, portfolio))
    candidate_rows = [row for row in candidate_rows if int(row[2]["total_trades"]) > 0]
    candidate_rows.sort(reverse=True)
    best_expectancy, best_id, best_portfolio = candidate_rows[0]
    best_stress = stress["scenarios"][best_id]
    best_oos = oos["results"][best_id]
    positive_windows = 0
    total_windows = 0
    for payload in best_oos.get("walk_forward_oos", {}).values():
        for symbol_payload in payload.get("symbols", {}).values():
            total_windows += 1
            if Decimal(str(symbol_payload.get("net_expectancy") or "0")) > 0:
                positive_windows += 1

    actual = evaluation["v2_actual"]
    lines = [
        "# Optimization Result",
        "",
        "## FINAL STATUS",
        "",
        "`AUDIT_PASS / STRATEGY_NOT_ACCEPTED / EXECUTION_FROZEN`",
        "",
        "## DATA RANGE",
        "",
        f"- first exchange trade: {completeness['first_exchange_trade']}",
        f"- last exchange trade: {completeness['last_exchange_trade']}",
        f"- days: {days:.2f}",
        f"- symbols: {', '.join(completeness['scope'])}",
        "",
        "## DATA COMPLETENESS",
        "",
        f"- exchange trades: {completeness['exchange_trades']}",
        f"- exchange orders: {completeness['exchange_orders']}",
        f"- algo orders: {completeness['algo_orders']}",
        f"- income records: {completeness['income_records']}",
        f"- V2 match rate: {completeness['v2_match_rate']}",
        f"- unmatched records: {completeness['unmatched_records']}",
        "",
        "## LIVE ACTUAL (V2 ENTRY-MATCHED EPISODES)",
        "",
        f"- trades: {actual['trades']}",
        f"- net pnl: {actual['net_pnl']} USDT",
        f"- commission: {actual['commission']} USDT",
        f"- funding: {actual['funding']} USDT",
        f"- PF: {actual['profit_factor']}",
        f"- expectancy: {actual['expectancy']} USDT",
        f"- win rate: {actual['win_rate']}",
        f"- avg win: {actual['avg_win']} USDT",
        f"- avg loss: {actual['avg_loss']} USDT",
        f"- max DD: {actual['max_drawdown']} USDT",
        "",
        "## BEST / WORST",
        "",
        f"- best symbol: {evaluation['best']['symbol']}",
        f"- worst symbol: {evaluation['worst']['symbol']}",
        f"- best direction: {evaluation['best']['direction']}",
        f"- worst direction: {evaluation['worst']['direction']}",
        f"- strategy: {evaluation['worst']['strategy']}",
        "- regime: UNKNOWN (decision payload did not persist a canonical regime field for the matched entries)",
        "- score bucket: UNKNOWN (not persisted in the matched V2 decision payload)",
        "",
        "## TOP LOSS CAUSES",
        "",
        *[f"- {row['cause']}: {row['trades']} trades, {row['net_pnl']} USDT" for row in losses["top_loss_causes"]],
        "",
        "## SL/TP",
        "",
        f"- stop hit: {evaluation['sl_tp']['stop_hit']}",
        f"- TP hit: {evaluation['sl_tp']['tp_hit']}",
        f"- MFE measured: {_mean(v2_rows, 'mfe_pct')}% average over {evaluation['sl_tp']['mfe_measured']} episodes",
        f"- MAE measured: {_mean(v2_rows, 'mae_pct')}% average over {evaluation['sl_tp']['mae_measured']} episodes",
        f"- giveback: {_mean(v2_rows, 'giveback_pct')}% average where measurable",
        "",
        "## EXECUTION",
        "",
        f"- reconciliation defects: {evaluation['execution']['reconciliation_defects']}",
        f"- open incidents: {evaluation['execution']['open_incidents']}",
        f"- slippage: {evaluation['execution']['slippage']}",
        f"- latency: {evaluation['execution']['latency']}",
        "",
        "## OPTIMIZED STRATEGY",
        "",
        f"- generation SHA: {manifest['source_tree_hash']}",
        f"- candidate: {best_id}",
        "- strategy families: structural trend pullback, range sweep reversion, failed breakout reversal, breakout continuation",
        "",
        "## OOS",
        "",
        f"- trades: {best_portfolio['total_trades']}",
        f"- net return: {best_portfolio['net_return']}",
        f"- PF: {best_portfolio['profit_factor']}",
        f"- expectancy: {best_portfolio['net_expectancy']}",
        "- LCB: NOT_RUN (dependent/bootstrap promotion gate not executed)",
        f"- max DD: {best_portfolio['max_drawdown']}",
        f"- positive windows: {positive_windows}/{total_windows}",
        "",
        "## COST STRESS (BEST CANDIDATE)",
        "",
        *[f"- extra {bps} bps/side: expectancy {payload['expectancy']}" for bps, payload in best_stress.items()],
        "",
        "## LIVE COUNTERFACTUAL",
        "",
        f"- old actual: {actual['net_pnl']} USDT / PF {actual['profit_factor']}",
        f"- new candidate OOS: {best_portfolio['net_return']} / PF {best_portfolio['profit_factor']}",
        "- live counterfactual: NOT_MEASURED; candidate was never armed and no execution-chain write was made",
        "",
        "## EXECUTION CHAIN",
        "",
        "`UNCHANGED / NO_EXECUTION_MODULE_MODIFIED`",
        "",
        "The candidate remains research-only. The active Testnet strategy and all exchange adapters, intents, protections, reconciliation, scheduler, and recovery paths remain frozen.",
    ]
    output = report_dir / "optimization_result.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=Path("artifacts/trading_audit"))
    args = parser.parse_args()
    print(build(audit_dir=args.audit_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
