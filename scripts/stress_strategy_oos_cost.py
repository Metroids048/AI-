"""Apply transparent extra round-trip cost stress to a research replay artifact."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any


def _metrics(returns: list[Decimal]) -> dict[str, Any]:
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_loss = abs(sum(losses, Decimal("0")))
    return {
        "trades": len(returns),
        "net_return": str(sum(returns, Decimal("0"))),
        "expectancy": str(sum(returns, Decimal("0")) / Decimal(len(returns))) if returns else None,
        "profit_factor": str(sum(wins, Decimal("0")) / gross_loss) if gross_loss else None,
        "win_rate": str(Decimal(len(wins)) / Decimal(len(returns))) if returns else None,
    }


def stress(*, report_path: Path, output_path: Path, extra_per_side_bps: tuple[Decimal, ...]) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    results: dict[str, Any] = {}
    for candidate_id, candidate in report["results"].items():
        trades = candidate.get("trades", [])
        scenarios: dict[str, Any] = {}
        for extra_bps in extra_per_side_bps:
            returns = []
            for trade in trades:
                base = Decimal(str(trade["net_return"]))
                filled_fraction = Decimal(str(trade.get("filled_fraction") or "1"))
                extra_round_trip = extra_bps * Decimal("2") / Decimal("10000") * filled_fraction
                returns.append(base - extra_round_trip)
            scenarios[str(extra_bps)] = _metrics(returns)
        results[candidate_id] = scenarios
    output = {
        "source": str(report_path),
        "extra_cost_unit": "bps per side, added to the replay's configured cost model",
        "scenarios": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = stress(
        report_path=args.report,
        output_path=args.output,
        extra_per_side_bps=(Decimal("0"), Decimal("5"), Decimal("10"), Decimal("20")),
    )
    print(json.dumps(result["scenarios"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
