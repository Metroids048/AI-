"""Generate a hash-bound QuantDinger Shadow replay artifact.

This command is offline-only. It reads a strategy source file and a JSON
historical-bar mapping, runs the constrained subprocess worker, and writes a
structured artifact. It never imports an execution gateway or writes runtime
positions/orders.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.strategy_library.adapters.quantdinger_shadow_runtime import run_quantdinger_shadow_source


def build_artifact(
    *,
    source_path: Path,
    bars_path: Path,
    output_path: Path,
    strategy_id: str,
    strategy_version: str,
) -> dict[str, Any]:
    source = source_path.read_text(encoding="utf-8")
    bars = json.loads(bars_path.read_text(encoding="utf-8"))
    if not isinstance(bars, dict):
        raise ValueError("bars JSON must be an object keyed by symbol")
    run = run_quantdinger_shadow_source(
        source,
        bars_by_symbol=bars,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
    )
    trades = run.replay_trades(bars_by_symbol=bars)
    artifact: dict[str, Any] = {
        "schema_version": "1",
        "source_name": run.manifest.source_name,
        "source_version": run.manifest.source_version,
        "manifest_code_hash": run.manifest.code_hash,
        "timeframe": run.manifest.timeframe,
        "warmup_bars": run.manifest.warmup_bars,
        "stop_loss_pct": str(run.manifest.protection.stop_loss_pct)
        if run.manifest.protection.stop_loss_pct is not None
        else None,
        "take_profit_pct": str(run.manifest.protection.take_profit_pct)
        if run.manifest.protection.take_profit_pct is not None
        else None,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "signals": [
            {
                "manifest_code_hash": run.manifest.code_hash,
                "strategy_id": signal.strategy_id,
                "strategy_version": signal.strategy_version,
                "symbol": signal.symbol,
                "timeframe": signal.timeframe,
                "side": signal.side,
                "signal_candle_close_time": signal.signal_candle_close_time.isoformat(),
                "signal_reference_price": str(signal.signal_reference_price),
                "confidence": str(signal.confidence),
                "stop_distance": str(signal.stop_distance),
                "take_profit_distance": (
                    str(signal.take_profit_distance) if signal.take_profit_distance is not None else None
                ),
                "max_entry_drift_bps": str(signal.max_entry_drift_bps),
                "expires_at": signal.expires_at.isoformat(),
            }
            for signal in run.signals
        ],
        "trades": [
            {
                "symbol": trade.symbol,
                "side": trade.side,
                "signal_candle_close_time": trade.signal_candle_close_time.isoformat(),
                "entry_time": trade.entry_time.isoformat(),
                "entry_price": str(trade.entry_price),
                "stop_price": str(trade.stop_price),
                "take_price": str(trade.take_price),
                "exit_time": trade.exit_time.isoformat(),
                "exit_price": str(trade.exit_price),
                "exit_reason": trade.exit_reason,
                "fee_bps": str(trade.fee_bps),
            }
            for trade in trades
        ],
        "rejected_events": list(run.rejected_events),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bars-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strategy-id", default="quantdinger_shadow")
    parser.add_argument("--strategy-version", default="5.0.1")
    args = parser.parse_args()
    artifact = build_artifact(
        source_path=args.source,
        bars_path=args.bars_json,
        output_path=args.output,
        strategy_id=args.strategy_id,
        strategy_version=args.strategy_version,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "manifest_code_hash": artifact["manifest_code_hash"],
                "signals": len(artifact["signals"]),
                "trades": len(artifact["trades"]),
                "output": str(args.output),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
