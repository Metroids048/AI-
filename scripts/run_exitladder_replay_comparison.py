"""Compare fixed 2R exits vs ExitLadder on the same layered entry policy.

Evidence-only: writes an audit report and never mutates auto-trading config.
"""

from __future__ import annotations

import argparse
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    TechnicalStrategyValidationService,
)
from shared.models import Timeframe


def _candidate_rules(*, with_ladder: bool) -> dict:
    rules = deepcopy(AUTO_PAPER_TECHNICAL_RULES)
    if with_ladder:
        return rules
    rules["exit_rules"] = {}
    rules["takeprofit_rules"] = {"risk_reward": float(AUTO_PAPER_TECHNICAL_RULES["takeprofit_rules"]["risk_reward"])}
    return rules


def _write_markdown(*, fixed: object, ladder: object, destination: Path) -> None:
    lines = [
        "# ExitLadder vs Fixed 2R Replay (same layered entry)",
        "",
        f"- Generated at: {datetime.now(UTC).isoformat()}",
        f"- Candidate entry: `{AUTO_PAPER_TECHNICAL_KEY}` (4h/1h/15m + layered_regime_entry)",
        "- Automatic Paper/Testnet settings: unchanged",
        "- Promotion: not requested; report is evidence only",
        "",
        "## Comparison",
        "",
        "| Metric | Fixed 2R | ExitLadder |",
        "| --- | ---: | ---: |",
        f"| Signals | {fixed.signal_count} | {ladder.signal_count} |",
        f"| Trade slices | {fixed.total_trades} | {ladder.total_trades} |",
        f"| Win rate | {fixed.win_rate:.4f} | {ladder.win_rate:.4f} |",
        f"| Net return | {fixed.net_return:.6f} | {ladder.net_return:.6f} |",
        f"| Net expectancy | {fixed.net_expectancy:.6f} | {ladder.net_expectancy:.6f} |",
        f"| Profit factor | {fixed.profit_factor:.4f} | {ladder.profit_factor:.4f} |",
        f"| Max drawdown | {fixed.max_drawdown:.4f} | {ladder.max_drawdown:.4f} |",
        f"| Avg hold hours | {fixed.average_hold_hours:.2f} | {ladder.average_hold_hours:.2f} |",
        f"| Ladder hits | {fixed.ladder_level_hits} | {ladder.ladder_level_hits} |",
        "",
        "## Notes",
        "",
        "- Fixed 2R path isolates entry quality (legacy prescreen).",
        "- ExitLadder path uses AUTO_PAPER_TECHNICAL_RULES.takeprofit_rules.exit_ladder.",
        "- Failed Validation gates still forbid auto promotion.",
        "",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare fixed 2R vs ExitLadder exits.")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--reuse-stored-data", action="store_true")
    args = parser.parse_args()
    if args.days < 60:
        raise SystemExit("--days must be at least 60")

    root = Path(__file__).resolve().parents[1]
    default_database_url = f"sqlite:///{(root / '.local' / 'technical-validation.db').as_posix()}"
    database_url = args.database_url or default_database_url
    os.environ["POSTGRES_URL"] = database_url
    from scripts.prepare_database import prepare_database

    prepare_database(database_url)
    end_at = _closed_four_hour_boundary(datetime.now(UTC))
    market_data = (
        _load_stored(days=args.days, end_at=end_at)
        if args.reuse_stored_data
        else _load_or_backfill(days=args.days, end_at=end_at)
    )

    fixed_strategy = _template(
        strategy_key=f"{AUTO_PAPER_TECHNICAL_KEY}_fixed_2r",
        rules=_candidate_rules(with_ladder=False),
        timeframe=Timeframe.M15,
    )
    ladder_strategy = _template(
        strategy_key=f"{AUTO_PAPER_TECHNICAL_KEY}_exit_ladder",
        rules=_candidate_rules(with_ladder=True),
        timeframe=Timeframe.M15,
    )

    fixed_metrics = TechnicalStrategyValidationService(
        max_workers=8, exit_mode=EXIT_MODE_FIXED_2R
    ).replay(strategy=fixed_strategy, market_data=market_data)
    ladder_metrics = TechnicalStrategyValidationService(
        max_workers=8, exit_mode=EXIT_MODE_EXIT_LADDER
    ).replay(strategy=ladder_strategy, market_data=market_data)

    output = args.output or (
        root / "docs" / "audits" / f"{datetime.now(UTC).date().isoformat()}-exitladder-replay-comparison.md"
    )
    _write_markdown(fixed=fixed_metrics, ladder=ladder_metrics, destination=output)
    print(output)
    print(f"fixed_net_expectancy={fixed_metrics.net_expectancy:.6f}")
    print(f"ladder_net_expectancy={ladder_metrics.net_expectancy:.6f}")
    print(f"ladder_hits={ladder_metrics.ladder_level_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
