"""Generic exit-policy A/B replay CLI (module 7 of the phased plan).

Replays one fixed entry signal under two exit policies over the fixed Top20
Binance USD-M basket and writes an evidence-only audit report. It never mutates
automatic Paper/Testnet execution configuration.

Two entry baselines are selectable:

* ``live`` (default): the current :data:`AUTO_PAPER_TECHNICAL_RULES` entry side.
* ``frozen-2026-07-12``: the exact entry config in effect when
  ``docs/audits/2026-07-12-exitladder-replay-comparison.md`` was generated
  (8 signals, 10/0 & 18/0 bps costs). Combined with ``--end-at`` pinned to the
  audit boundary and ``--reuse-stored-data``, this reproduces the audit numbers
  for regression verification. Config drifted after the audit (10 signals,
  5/1 & 5/3 bps, ladder reverted), so ``live`` no longer reproduces them.
"""

from __future__ import annotations

import argparse
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_top20_technical_validation import (
    _closed_four_hour_boundary,
    _load_or_backfill,
    _load_stored,
    _template,
)
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, AUTO_PAPER_TECHNICAL_RULES
from services.validation.technical_replay import (
    EXIT_MODE_EXIT_LADDER,
    EXIT_MODE_FIXED_2R,
    ExitPolicy,
    ExitPolicyComparisonReport,
    MarketData,
    compare_exit_policies,
)
from shared.models import Timeframe

# Exact AUTO_PAPER_TECHNICAL_RULES as of commit 3267c29, when the 2026-07-12
# ExitLadder-vs-Fixed2R audit was generated. Frozen here so regression runs stay
# reproducible even as the live runtime config evolves. DO NOT "sync" this to the
# live rules -- its whole purpose is to preserve the audit-time entry signal.
FROZEN_2026_07_12_TECHNICAL_RULES: dict[str, Any] = {
    "entry_rules": {
        "technical_pipeline": True,
        "strategy_lanes": ["trend_breakout", "volatility_filtered_breakout"],
        "timeframe_model": "4h_direction_15m_entry",
        "direction_timeframe": "4h",
        "state_timeframe": "1h",
        "entry_timeframe": "15m",
        "enabled_signals": [
            "macd",
            "dow_trend",
            "ema_trend",
            "adx",
            "price_action",
            "rsi",
            "vwap",
            "bollinger",
        ],
        "meta_label_min_win_rate": 0.50,
        "fusion_method": "layered_regime_entry",
        "core_fee_bps": 10.0,
        "core_slippage_bps": 0.0,
        "standard_fee_bps": 18.0,
        "standard_slippage_bps": 0.0,
        "minimum_net_reward_r": 1.0,
    },
    "exit_rules": {"close_on_opposite_signal": True, "time_exit_hours": 24, "time_exit_min_r": 0.5},
    "stoploss_rules": {"atr_multiple": 2.0, "fixed_bps": 250},
    "takeprofit_rules": {
        "risk_reward": 2.0,
        "break_even_after_r": 1.0,
        "partial_take_profit_r": 2.0,
        "partial_close_fraction": 0.5,
        "atr_trailing_multiple": 2.0,
        "exit_ladder": [
            {"r_multiple": 1.0, "close_fraction": 0.4},
            {"r_multiple": 1.5, "close_fraction": 0.3},
        ],
        "remainder_trail_after_r": 2.5,
    },
    "position_rules": {
        "risk_per_trade": 0.02,
        "max_portfolio_initial_risk_fraction": 0.10,
        "max_leverage": 20,
        "max_position_fraction": 0.15,
        "min_notional_usdt": 20,
    },
}

# Canonical partial-exit ladder, injected for the ladder arm when the chosen
# entry baseline no longer ships one (e.g. the live config after the 2026-07
# revert). Matches the ladder that produced the 2026-07-12 audit.
CANONICAL_EXIT_LADDER_LEVELS: list[dict[str, float]] = [
    {"r_multiple": 1.0, "close_fraction": 0.4},
    {"r_multiple": 1.5, "close_fraction": 0.3},
]
CANONICAL_REMAINDER_TRAIL_AFTER_R = 2.5

ENTRY_BASELINE_LIVE = "live"
ENTRY_BASELINE_FROZEN = "frozen-2026-07-12"


def _base_rules(entry_baseline: str) -> dict[str, Any]:
    if entry_baseline == ENTRY_BASELINE_FROZEN:
        return deepcopy(FROZEN_2026_07_12_TECHNICAL_RULES)
    return deepcopy(AUTO_PAPER_TECHNICAL_RULES)


def build_fixed_and_ladder_policies(base_rules: dict[str, Any]) -> tuple[ExitPolicy, ExitPolicy]:
    """Derive a Fixed-2R and an ExitLadder policy from a shared entry baseline.

    Mirrors the legacy ``_candidate_rules`` split: Fixed 2R strips the exit side
    to a plain full-close risk_reward; ExitLadder keeps (or, if the baseline has
    since dropped it, re-injects) the partial-close ladder. The originating dict
    is never mutated.
    """

    takeprofit = base_rules["takeprofit_rules"]
    risk_reward = float(takeprofit.get("risk_reward", 2.0))
    fixed = ExitPolicy(
        name="Fixed 2R",
        exit_mode=EXIT_MODE_FIXED_2R,
        exit_rules={},
        takeprofit_rules={"risk_reward": risk_reward},
    )
    ladder_takeprofit = deepcopy(takeprofit)
    if "exit_ladder" not in ladder_takeprofit:
        ladder_takeprofit["exit_ladder"] = deepcopy(CANONICAL_EXIT_LADDER_LEVELS)
        ladder_takeprofit.setdefault("remainder_trail_after_r", CANONICAL_REMAINDER_TRAIL_AFTER_R)
    ladder = ExitPolicy(
        name="ExitLadder",
        exit_mode=EXIT_MODE_EXIT_LADDER,
        exit_rules=deepcopy(base_rules.get("exit_rules", {})),
        takeprofit_rules=ladder_takeprofit,
    )
    return fixed, ladder


def run_comparison(
    *,
    market_data: MarketData,
    entry_baseline: str,
    max_workers: int = 8,
) -> ExitPolicyComparisonReport:
    base_rules = _base_rules(entry_baseline)
    entry_config = _template(
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        rules=base_rules,
        timeframe=Timeframe.M15,
    )
    fixed_policy, ladder_policy = build_fixed_and_ladder_policies(base_rules)
    # policy_a=Fixed 2R (left column), policy_b=ExitLadder (right), matching the
    # committed audit table ordering.
    return compare_exit_policies(
        entry_config=entry_config,
        exit_policy_a=fixed_policy,
        exit_policy_b=ladder_policy,
        market_data=market_data,
        title="ExitLadder vs Fixed 2R Replay (same layered entry)",
        entry_label=f"{AUTO_PAPER_TECHNICAL_KEY} ({entry_baseline})",
        max_workers=max_workers,
    )


def _parse_end_at(raw: str | None) -> datetime:
    if not raw:
        return _closed_four_hour_boundary(datetime.now(UTC))
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic exit-policy A/B replay (Fixed 2R vs ExitLadder).")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument(
        "--end-at",
        default=None,
        help="ISO timestamp to pin the replay window end (UTC assumed). "
        "Use 2026-07-12T08:00:00 to reproduce the audit. Defaults to the last closed 4h boundary.",
    )
    parser.add_argument(
        "--entry-baseline",
        choices=(ENTRY_BASELINE_LIVE, ENTRY_BASELINE_FROZEN),
        default=ENTRY_BASELINE_LIVE,
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--reuse-stored-data", action="store_true")
    parser.add_argument("--max-workers", type=int, default=8)
    args = parser.parse_args()
    if args.days < 60:
        raise SystemExit("--days must be at least 60 to retain 4h warmup and an OOS window")

    root = Path(__file__).resolve().parents[1]
    default_database_url = f"sqlite:///{(root / '.local' / 'technical-validation.db').as_posix()}"
    database_url = args.database_url or default_database_url
    os.environ["POSTGRES_URL"] = database_url
    from scripts.prepare_database import prepare_database

    prepare_database(database_url)
    end_at = _parse_end_at(args.end_at)
    market_data = (
        _load_stored(days=args.days, end_at=end_at)
        if args.reuse_stored_data
        else _load_or_backfill(days=args.days, end_at=end_at)
    )

    report = run_comparison(
        market_data=market_data,
        entry_baseline=args.entry_baseline,
        max_workers=args.max_workers,
    )

    output = args.output or (
        root / "docs" / "audits" / f"{datetime.now(UTC).date().isoformat()}-exitladder-replay-comparison.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.to_markdown(), encoding="utf-8")
    print(output)
    print(f"entry_baseline={args.entry_baseline}")
    print(f"end_at={end_at.isoformat()}")
    print(f"fixed_signals={report.policy_a.signal_count} ladder_signals={report.policy_b.signal_count}")
    print(f"fixed_net_expectancy={report.policy_a.net_expectancy:.6f}")
    print(f"ladder_net_expectancy={report.policy_b.net_expectancy:.6f}")
    print(f"ladder_hits={report.policy_b.ladder_level_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
