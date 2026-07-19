"""Read-only readiness monitor for operator candidates before Paper A/B admission."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from scripts.compute_signal_edge_stats import evidence_failure_reasons
from scripts.run_candidate_competition import _oos_metrics
from scripts.run_top20_technical_validation import _closed_four_hour_boundary, _load_stored, _template
from services.strategy_library.candidates.registry import get_candidate
from services.validation.technical_replay import TechnicalStrategyValidationService
from shared.models import Timeframe

SYMBOL = "BTC/USDT"
CANDIDATES = ("operator_heuristic_v1", "operator_heuristic_v2_relaxed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/audits"))
    args = parser.parse_args()
    os.environ["POSTGRES_URL"] = args.database_url
    market_data = _load_stored(
        days=args.days,
        end_at=_closed_four_hour_boundary(datetime.now(UTC)),
        symbols=(SYMBOL,),
    )
    rows = []
    for candidate_id in CANDIDATES:
        service = TechnicalStrategyValidationService(max_workers=1)
        strategy = _template(
            strategy_key=f"operator-monitor:{candidate_id}",
            rules=get_candidate(candidate_id).get_config(),
            timeframe=Timeframe.M15,
            symbols=(SYMBOL,),
        )
        full = service.replay(strategy=strategy, market_data=market_data)
        oos = _oos_metrics(service, full)
        failed = evidence_failure_reasons(oos)
        rows.append(
            {
                "candidate_id": candidate_id,
                "symbol": SYMBOL,
                "oos_sample_count": oos.total_trades,
                "net_expectancy": oos.net_expectancy,
                "sharpe": oos.sharpe,
                "profit_factor": oos.profit_factor,
                "max_drawdown": oos.max_drawdown,
                "failed_reasons": failed,
                "ready_for_shadow_ab": not failed,
            }
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{datetime.now(UTC).date().isoformat()}-operator-candidate-monitor.json"
    payload = {"generated_at": datetime.now(UTC).isoformat(), "rows": rows}
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(output)
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
