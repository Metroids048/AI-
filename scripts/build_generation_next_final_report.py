"""Build the final Generation-Next report from frozen audit and OOS artifacts."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any


def _d(value: object) -> Decimal:
    return Decimal(str(value))


def build(audit_dir: Path) -> None:
    report_dir = audit_dir / "reports"
    root_cause = json.loads((report_dir / "live_payoff_root_cause.json").read_text(encoding="utf-8"))
    completeness = json.loads((report_dir / "data_completeness.json").read_text(encoding="utf-8"))
    evaluation = json.loads((report_dir / "live_strategy_evaluation.json").read_text(encoding="utf-8"))
    generations = []
    generation_dirs = {
        1: "strategy_oos_generation_next_fast_g1d",
        2: "strategy_oos_generation_next_fast_g2",
        3: "strategy_oos_generation_next_fast_g3",
        4: "strategy_oos_generation_next_fast_g4",
        5: "strategy_oos_generation_next_fast_g5",
    }
    for generation in range(1, 6):
        payload = json.loads(
            (audit_dir / generation_dirs[generation] / "generation-report.json").read_text(encoding="utf-8")
        )
        generations.append(payload)

    latest = generations[-1]
    latest_family = "momentum_continuation_v1"
    latest_result = latest["results"][latest_family]
    buckets: dict[str, dict[str, Any]] = {}
    for trade in latest_result["trades"]:
        key = f"{trade['symbol']} {trade['side']}"
        bucket = buckets.setdefault(key, {"trades": 0, "net_return": "0", "wins": 0, "losses": 0})
        bucket["trades"] += 1
        bucket["net_return"] = str(_d(bucket["net_return"]) + _d(trade["net_return"]))
        if _d(trade["net_return"]) > 0:
            bucket["wins"] += 1
        elif _d(trade["net_return"]) < 0:
            bucket["losses"] += 1
    source_hashes = {str(payload["generation"]): payload["source_hash"] for payload in generations}
    loop = {
        "status": "STRATEGY_EDGE_NOT_FOUND",
        "live_root_cause": "LIVE_ROOT_CAUSE_COMPLETE",
        "payoff_inversion": "PAYOFF_INVERSION_EXPLAINED",
        "generations": [
            {
                "generation": payload["generation"],
                "source_hash": payload["source_hash"],
                "families": {family: result["portfolio"] for family, result in payload["results"].items()},
            }
            for payload in generations
        ],
        "new_strategy_oos": "FAIL",
        "cost_stress": "FAIL",
        "execution_regression": "PASS",
        "symbol_direction_buckets_generation_5": buckets,
        "live_counterfactual": "NOT_RUN_STRATEGY_REJECTED",
        "old_stop_episodes_avoided": "NOT_RUN_STRATEGY_REJECTED",
    }
    (report_dir / "strategy_generation_loop.json").write_text(json.dumps(loop, indent=2) + "\n", encoding="utf-8")

    all_root = root_cause["all"]
    causes = root_cause["root_causes"]
    lines = [
        "FINAL STATUS",
        "",
        "STRATEGY_EDGE_NOT_FOUND",
        "",
        "LIVE ROOT CAUSE:",
        "48% win rate仍亏 -454.1140 USDT，因为亏损单的风险基数、成本和低质量入场共同压过了盈利单。",
        f"Payoff inversion: sizing risk ratio {causes['LOSS_SIZE_IMBALANCE']['loser_to_winner_risk_ratio']}x; "
        f"planned RR winners/losers {root_cause['winners']['average_planned_rr']} / {root_cause['losers']['average_planned_rr']}; "
        f"cost drag {causes['COST_DRAG']['total_cost_drag_usdt']} USDT; "
        f"stop MFE {causes['ENTRY_BAD_LOCATION']['stop_average_MFE_R']}R; "
        f"giveback {causes['PROFIT_GIVEBACK']['average_giveback_R']}R.",
        "",
        "R-NORMALIZED:",
        f"gross mean {all_root['average_gross_R']}R; net mean {all_root['average_net_R']}R; "
        f"winner net {root_cause['winners']['average_net_R']}R; loser net {root_cause['losers']['average_net_R']}R.",
        "",
        "NEW STRATEGY:",
        f"generation 5 SHA {source_hashes['5']}; family {latest_family}; research-only, rejected.",
        "",
        "OOS:",
        f"trades {latest_result['portfolio']['trades']}; return {latest_result['portfolio']['return']}; "
        f"PF {latest_result['portfolio']['PF']}; expectancy {latest_result['portfolio']['expectancy']}; "
        f"LCB {latest_result['portfolio']['LCB']}; max DD {latest_result['portfolio']['max_DD']}; "
        f"positive windows {latest_result['portfolio']['positive_windows']}/8.",
        "",
        "SYMBOL/DIRECTION (GENERATION 5):",
    ]
    for key in ("BTC/USDT long", "BTC/USDT short", "ETH/USDT long", "ETH/USDT short"):
        lines.append(f"{key}: {json.dumps(buckets.get(key, {'trades': 0}), sort_keys=True)}")
    lines += [
        "",
        "COST:",
        *[
            f"{multiplier}x: expectancy {latest['cost_scenarios'][latest_family][multiplier]['expectancy']}; "
            f"PF {latest['cost_scenarios'][latest_family][multiplier]['PF']}"
            for multiplier in ("1", "1.5", "2")
        ],
        "",
        "LIVE COUNTERFACTUAL:",
        "NOT_RUN_STRATEGY_REJECTED; no rejected candidate was replayed as a promotion counterfactual.",
        "旧 12 个 STOP: 新策略避免数未运行，因为没有 candidate 通过 standalone OOS gate。",
        "",
        "EXECUTION:",
        "UNCHANGED / REGRESSION_PASS",
        "",
        "RECONCILIATION:",
        "SEPARATE_EXECUTION_AUDIT_REQUIRED / NO_ACTION",
        f"1773 reconciliation defects; 12 open incidents; exchange trades {completeness['exchange_trades']}; V2 match rate {completeness['v2_match_rate']}.",
    ]
    (report_dir / "optimization_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build(Path("artifacts/trading_audit"))
