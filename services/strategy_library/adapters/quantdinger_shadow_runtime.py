"""Isolated, Shadow-only execution of a constrained QuantDinger strategy.

The worker is intentionally separate from the API, scheduler, and Binance
adapter. It receives only serialized historical bars, runs the strategy with a
small in-memory context, captures order intent as structured signals, and
returns no exchange primitive. The parent process then applies the existing
manifest and candidate contracts before any funnel observation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.automated_trading.domain.candidates import TradeCandidate

from .quantdinger import (
    QuantDingerSignal,
    QuantDingerStrategyManifest,
    build_shadow_candidate,
    compile_strategy_manifest,
    parse_shadow_signal,
)


class QuantDingerShadowRuntimeError(RuntimeError):
    """Raised when the isolated Shadow worker cannot produce a valid result."""


@dataclass(frozen=True)
class QuantDingerShadowRun:
    """A completed, offline-only run and its validated signals."""

    manifest: QuantDingerStrategyManifest
    signals: tuple[QuantDingerSignal, ...]
    rejected_events: tuple[dict[str, str], ...] = ()

    def candidates(self, *, cycle_id: str) -> tuple[TradeCandidate, ...]:
        """Convert signals to the existing non-promotable Shadow contract."""
        return tuple(build_shadow_candidate(self.manifest, signal, cycle_id=cycle_id) for signal in self.signals)

    def replay_trades(
        self,
        *,
        bars_by_symbol: Mapping[str, Sequence[Mapping[str, object]]],
        fee_bps: Decimal = Decimal("5"),
    ):
        """Replay captured signals with closed-bar -> next-bar semantics.

        This is an external Shadow replay only. Formal promotion still requires
        comparing its result with ``TechnicalStrategyValidationService``.
        """
        from services.validation.quantdinger_differential_replay import QuantDingerReplayTrade

        if not fee_bps.is_finite() or fee_bps < 0:
            raise ValueError("fee_bps must be finite and non-negative")
        interval = _timeframe_delta(self.manifest.timeframe)
        output: list[QuantDingerReplayTrade] = []
        seen: set[tuple[str, str, datetime]] = set()
        for signal in self.signals:
            rows = _normalized_replay_bars(bars_by_symbol.get(signal.symbol, ()))
            entry_time = signal.signal_candle_close_time + interval
            entry_index = next((index for index, row in enumerate(rows) if row["timestamp"] == entry_time), None)
            if entry_index is None:
                continue
            key = (signal.symbol, signal.side.lower(), entry_time)
            if key in seen:
                continue
            seen.add(key)
            entry_bar = rows[entry_index]
            entry_price = entry_bar["open"]
            stop_price = (
                entry_price - signal.stop_distance if signal.side == "LONG" else entry_price + signal.stop_distance
            )
            take_price = (
                entry_price + signal.take_profit_distance
                if signal.side == "LONG" and signal.take_profit_distance is not None
                else entry_price - signal.take_profit_distance
                if signal.side == "SHORT" and signal.take_profit_distance is not None
                else stop_price
            )
            exit_time = rows[-1]["timestamp"]
            exit_price = rows[-1]["close"]
            exit_reason = "end_of_window"
            for row in rows[entry_index + 1 :]:
                stop_hit = row["low"] <= stop_price if signal.side == "LONG" else row["high"] >= stop_price
                take_hit = row["high"] >= take_price if signal.side == "LONG" else row["low"] <= take_price
                if stop_hit:
                    exit_time, exit_price, exit_reason = row["timestamp"], stop_price, "stoploss"
                    break
                if signal.take_profit_distance is not None and take_hit:
                    exit_time, exit_price, exit_reason = row["timestamp"], take_price, "takeprofit"
                    break
            output.append(
                QuantDingerReplayTrade(
                    symbol=signal.symbol,
                    side=signal.side,
                    signal_candle_close_time=signal.signal_candle_close_time,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    take_price=take_price,
                    exit_time=exit_time,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    fee_bps=fee_bps,
                )
            )
        return tuple(output)


def run_quantdinger_shadow_source(
    source: str,
    *,
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, object]]],
    strategy_id: str = "quantdinger_shadow",
    strategy_version: str = "5.0.1",
    params: Mapping[str, object] | None = None,
    timeout_seconds: float = 5.0,
) -> QuantDingerShadowRun:
    """Run a strategy in a bounded subprocess and capture only Shadow signals.

    ``compile_strategy_manifest`` remains the source contract. The worker opts
    into order API names only because those calls are intercepted and converted
    to signals; no ``adapter``, credential, database session, or local position
    is present in the child process.
    """
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("timeout_seconds must be in (0, 60]")
    manifest = compile_strategy_manifest(source, allow_shadow_order_apis=True)
    if not bars_by_symbol:
        raise QuantDingerShadowRuntimeError("at least one historical symbol series is required")
    payload = {
        "source": source,
        "manifest_code_hash": manifest.code_hash,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "manifest": {
            "timeframe": manifest.timeframe,
            "stop_loss_pct": str(manifest.protection.stop_loss_pct)
            if manifest.protection.stop_loss_pct is not None
            else None,
            "take_profit_pct": str(manifest.protection.take_profit_pct)
            if manifest.protection.take_profit_pct is not None
            else None,
        },
        "params": dict(params or {}),
        "bars_by_symbol": _serialize_bars(bars_by_symbol),
    }
    root = Path(__file__).resolve().parents[3]
    child_env = {
        "PYTHONPATH": str(root),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("quantdinger_shadow_worker.py"))],
            cwd=root,
            input=json.dumps(payload, ensure_ascii=True),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise QuantDingerShadowRuntimeError("isolated QuantDinger Shadow worker timed out") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "worker failed").strip()[-2000:]
        raise QuantDingerShadowRuntimeError(f"isolated QuantDinger Shadow worker failed: {detail}")
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise QuantDingerShadowRuntimeError("isolated worker returned invalid JSON") from exc
    if not isinstance(output, Mapping) or output.get("manifest_code_hash") != manifest.code_hash:
        raise QuantDingerShadowRuntimeError("isolated worker manifest hash did not match the compiled source")
    raw_signals = output.get("signals")
    if not isinstance(raw_signals, list):
        raise QuantDingerShadowRuntimeError("isolated worker signals must be a list")
    signals = tuple(parse_shadow_signal(item, manifest=manifest) for item in raw_signals)
    raw_rejected = output.get("rejected_events") or []
    if not isinstance(raw_rejected, list) or not all(isinstance(item, Mapping) for item in raw_rejected):
        raise QuantDingerShadowRuntimeError("isolated worker rejected_events must be objects")
    rejected = tuple({str(key): str(value) for key, value in item.items()} for item in raw_rejected)
    for signal in signals:
        if signal.strategy_id != strategy_id or signal.strategy_version != strategy_version:
            raise QuantDingerShadowRuntimeError("isolated worker returned unexpected strategy identity")
    return QuantDingerShadowRun(manifest=manifest, signals=signals, rejected_events=rejected)


def _serialize_bars(
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, list[dict[str, str]]]:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    result: dict[str, list[dict[str, str]]] = {}
    for symbol, rows in bars_by_symbol.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("historical bar symbol must be a non-empty string")
        serialized: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != required:
                raise ValueError(f"historical bar for {symbol} has an invalid schema")
            timestamp = row["timestamp"]
            if isinstance(timestamp, datetime):
                timestamp_value = timestamp.isoformat()
            elif isinstance(timestamp, str):
                timestamp_value = timestamp
            else:
                raise ValueError("historical bar timestamp must be datetime or ISO string")
            serialized.append(
                {
                    "timestamp": timestamp_value,
                    **{field: _finite_decimal_string(row[field], field) for field in required - {"timestamp"}},
                }
            )
        result[symbol] = serialized
    return result


def _finite_decimal_string(value: object, field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"historical bar {field} must be numeric")
    try:
        decimal_value = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"historical bar {field} must be numeric") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"historical bar {field} must be finite")
    return str(decimal_value)


def _timeframe_delta(timeframe: str):
    from datetime import timedelta

    return {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }[timeframe]


def _normalized_replay_bars(rows: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        timestamp = row["timestamp"]
        parsed = datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else timestamp
        if not isinstance(parsed, datetime) or parsed.tzinfo is None:
            raise ValueError("replay bars require timezone-aware timestamps")
        output.append(
            {
                "timestamp": parsed,
                "open": Decimal(str(row["open"])),
                "high": Decimal(str(row["high"])),
                "low": Decimal(str(row["low"])),
                "close": Decimal(str(row["close"])),
            }
        )
    return sorted(output, key=lambda item: item["timestamp"])
