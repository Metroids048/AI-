"""External vectorbt worker invoked only by :mod:`vectorbt_adapter`."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any


def _grid(parameter_space: dict[str, Any]) -> list[dict[str, Any]]:
    if not parameter_space:
        return [{}]
    keys = sorted(parameter_space)
    values = [value if isinstance(value, list) else [value] for value in (parameter_space[key] for key in keys)]
    return [dict(zip(keys, value, strict=True)) for value in itertools.product(*values)]


def _run(payload: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd
    import vectorbt as vbt

    rows = payload["rows"]
    options = payload.get("engine_options") or {}
    close_field = str(options.get("close_field") or "close")
    entry_field = str(options.get("entry_signal_field") or "entry_signal")
    exit_field = str(options.get("exit_signal_field") or "exit_signal")
    if not rows or any(close_field not in row or entry_field not in row or exit_field not in row for row in rows):
        raise ValueError("VECTORBT_CANONICAL_CLOSE_AND_SIGNAL_COLUMNS_REQUIRED")
    frame = pd.DataFrame(rows)
    close = pd.to_numeric(frame[close_field], errors="raise")
    entries = frame[entry_field].astype(bool)
    exits = frame[exit_field].astype(bool)
    fees = float((payload.get("cost_model") or {}).get("fee", 0.0))
    slippage = float((payload.get("cost_model") or {}).get("slippage", 0.0))
    portfolio = vbt.Portfolio.from_signals(
        close, entries, exits, fees=fees, slippage=slippage, freq=options.get("frequency")
    )
    trades = portfolio.trades.records_readable
    total_return = float(portfolio.total_return())
    trade_count = int(portfolio.trades.count())
    win_rate = float(portfolio.trades.win_rate()) if trade_count else 0.0
    profit_factor = float(portfolio.trades.profit_factor()) if trade_count else 0.0
    max_drawdown = abs(float(portfolio.max_drawdown()))
    payoff_ratio = None
    if trade_count and not trades.empty and "PnL" in trades:
        wins = trades.loc[trades["PnL"] > 0, "PnL"]
        losses = trades.loc[trades["PnL"] < 0, "PnL"]
        if not wins.empty and not losses.empty:
            payoff_ratio = float(wins.mean() / abs(losses.mean()))
    expectancy = total_return / trade_count if trade_count else 0.0
    candidates = [
        {
            "parameters": params,
            "trade_count": trade_count,
            "win_rate": win_rate,
            "payoff_ratio": payoff_ratio,
            "profit_factor": profit_factor,
            "expectancy_net_r": expectancy,
            "max_drawdown": max_drawdown,
        }
        for params in _grid(payload.get("parameter_space") or {})
    ]
    candidates.sort(key=lambda item: (_number(item["expectancy_net_r"]), _number(item["profit_factor"])), reverse=True)
    top = candidates[: max(1, int(payload.get("top_n", 10)))]
    threshold = _number(top[-1]["expectancy_net_r"])
    plateau_count = sum(_number(candidate["expectancy_net_r"]) >= threshold for candidate in candidates)
    return {
        "trade_count": trade_count,
        "win_rate": win_rate,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "expectancy_net_r": expectancy,
        "max_drawdown": max_drawdown,
        "parameter_plateau": {
            "top_candidates": top,
            "stable": plateau_count > 1,
            "candidate_count": len(candidates),
            "plateau_count": plateau_count,
            "neighbor_stability": plateau_count / len(candidates),
        },
        "metrics_by_window": {"canonical_dataset": {"return": total_return}},
    }


def _number(value: Any) -> float:
    return float(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(json.dumps(_run(payload), sort_keys=True))


if __name__ == "__main__":
    main()
