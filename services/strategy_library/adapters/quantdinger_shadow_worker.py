"""Private subprocess entry point for the QuantDinger Shadow adapter."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pandas as pd

_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}
_DANGEROUS_NAMES = frozenset({"__import__", "eval", "exec", "compile", "open", "input", "globals", "locals"})
_TIMEFRAME_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        output = _run(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    json.dump(output, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    return 0


def _run(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("worker payload must be an object")
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip() or len(source) > 100_000:
        raise ValueError("strategy source is missing or too large")
    _validate_source(source)
    manifest_code_hash = payload.get("manifest_code_hash")
    manifest_payload = payload.get("manifest")
    if not isinstance(manifest_code_hash, str) or not isinstance(manifest_payload, dict):
        raise ValueError("manifest contract is missing")
    computed_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if manifest_code_hash != computed_hash:
        raise ValueError("manifest hash does not match strategy source")
    timeframe = manifest_payload.get("timeframe")
    if not isinstance(timeframe, str) or timeframe not in _TIMEFRAME_DELTAS:
        raise ValueError("manifest timeframe is unsupported")
    manifest = SimpleNamespace(
        code_hash=manifest_code_hash,
        timeframe=timeframe,
        protection=SimpleNamespace(
            stop_loss_pct=manifest_payload.get("stop_loss_pct"),
            take_profit_pct=manifest_payload.get("take_profit_pct"),
        ),
    )
    strategy_id = _required_string(payload, "strategy_id")
    strategy_version = _required_string(payload, "strategy_version")
    bars_by_symbol = _decode_bars(payload.get("bars_by_symbol"))
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    signals: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    runtime = _Runtime(
        bars_by_symbol=bars_by_symbol,
        params=params,
        manifest=manifest,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        signals=signals,
        rejected=rejected,
    )
    namespace = runtime.namespace(source)
    exec(compile(source, "<quantdinger-shadow>", "exec"), namespace, namespace)
    initialize = namespace.get("initialize")
    if not callable(initialize):
        raise ValueError("initialize handler is required")
    initialize(runtime.context)
    handler = namespace.get("handle_data") or namespace.get("on_rebalance")
    if not callable(handler):
        raise ValueError("handle_data or on_rebalance handler is required")
    events = sorted(
        ((row["timestamp"], symbol, row) for symbol, rows in bars_by_symbol.items() for row in rows),
        key=lambda item: (item[0], item[1]),
    )
    for _, symbol, row in events:
        runtime.current_symbol = symbol
        runtime.current_bar = row
        runtime.context.current_dt = row["timestamp"]
        handler(runtime.context, runtime.data)
    return {"manifest_code_hash": manifest.code_hash, "signals": signals, "rejected_events": rejected}


class _Runtime:
    def __init__(self, *, bars_by_symbol, params, manifest, strategy_id, strategy_version, signals, rejected):
        self.bars_by_symbol = bars_by_symbol
        self.params = params
        self.manifest = manifest
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version
        self.signals = signals
        self.rejected = rejected
        self.positions: dict[str, float] = {}
        self.current_symbol = ""
        self.current_bar: dict[str, Any] | None = None
        self.state = SimpleNamespace()
        self.data = SimpleNamespace(history=self._history)
        self.context = _Context(self)

    def namespace(self, source: str) -> dict[str, object]:
        del source
        return {
            "__builtins__": _SAFE_BUILTINS,
            "g": self.state,
            "get_history": self._history,
            "history": self._history,
            "get_position": self._get_position,
            "get_positions": lambda: {
                symbol: SimpleNamespace(amount=amount, avg_cost=0.0, last_price=0.0)
                for symbol, amount in self.positions.items()
                if amount
            },
            "order": self._capture_order,
            "order_value": self._capture_order,
            "order_target": self._capture_order,
            "order_target_value": self._capture_order,
            "order_target_percent": self._capture_order,
            "log": lambda *_args, **_kwargs: None,
            "indicator": self._indicator,
            "factor": self._indicator,
        }

    def _history(self, count: object, frequency: object = None, field: object = "close", symbol: object = None, **_):
        del frequency
        key = str(symbol or self.current_symbol)
        rows = self.bars_by_symbol.get(key) or self.bars_by_symbol.get(_canonical_symbol(key), [])
        visible = [row for row in rows if self.current_bar is None or row["timestamp"] <= self.current_bar["timestamp"]]
        frame = pd.DataFrame(visible[-int(str(count)) :])
        if frame.empty:
            return frame
        frame = frame.set_index("timestamp")
        if isinstance(field, str):
            return frame[field]
        fields = list(field) if isinstance(field, (list, tuple)) else [str(field)]
        return frame[fields]

    def _get_position(self, symbol: object = None, **_kwargs: object) -> SimpleNamespace:
        key = str(symbol or self.current_symbol)
        amount = self.positions.get(key, self.positions.get(_canonical_symbol(key), 0.0))
        return SimpleNamespace(amount=amount, avg_cost=0.0, last_price=0.0)

    def _indicator(self, name: object, symbol: object = None, **_params):
        series = self._history(200, symbol=symbol, field="close")
        if len(series) == 0:
            return 0.0
        name_text = str(name).lower()
        if name_text.startswith("sma") or name_text.startswith("ema"):
            period = int("".join(char for char in name_text if char.isdigit()) or "20")
            return float(series.tail(period).mean())
        return float(series.iloc[-1])

    def _capture_order(self, symbol: object, value: object, **kwargs: object) -> None:
        if self.current_bar is None:
            self.rejected.append({"reason": "order_outside_bar", "symbol": str(symbol)})
            return
        side_value = _order_direction(value)
        if side_value == 0:
            return
        symbol_text = str(symbol)
        canonical_symbol = _canonical_symbol(symbol_text)
        current_amount = self.positions.get(canonical_symbol, 0.0)
        if current_amount:
            if (current_amount > 0 and side_value > 0) or (current_amount < 0 and side_value < 0):
                self.rejected.append({"reason": "duplicate_target_while_position_open", "symbol": canonical_symbol})
                return
            self.rejected.append({"reason": "position_transition_requires_explicit_exit", "symbol": canonical_symbol})
            return
        reference = self._history(1, symbol=symbol_text, field="close")
        if len(reference) == 0:
            self.rejected.append({"reason": "no_visible_close", "symbol": symbol_text})
            return
        price = Decimal(str(reference.iloc[-1]))
        declared_stop = self.manifest.protection.stop_loss_pct
        declared_take = self.manifest.protection.take_profit_pct
        stop_pct = kwargs.get("stop_loss_pct", declared_stop)
        take_pct = kwargs.get("take_profit_pct", declared_take)
        if stop_pct is None:
            self.rejected.append({"reason": "missing_stop_loss", "symbol": symbol_text})
            return
        if Decimal(str(stop_pct)) != Decimal(str(declared_stop)) or (
            (take_pct is None) != (declared_take is None)
            or (take_pct is not None and Decimal(str(take_pct)) != Decimal(str(declared_take)))
        ):
            self.rejected.append({"reason": "order_protection_differs_from_manifest", "symbol": canonical_symbol})
            return
        stop_distance = price * Decimal(str(stop_pct))
        take_distance = price * Decimal(str(take_pct)) if take_pct is not None and Decimal(str(take_pct)) > 0 else None
        expires = self.current_bar["timestamp"] + _TIMEFRAME_DELTAS[self.manifest.timeframe]
        self.positions[canonical_symbol] = 1.0 if side_value > 0 else -1.0
        self.signals.append(
            {
                "manifest_code_hash": self.manifest.code_hash,
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "symbol": canonical_symbol,
                "timeframe": self.manifest.timeframe,
                "side": "LONG" if side_value > 0 else "SHORT",
                "signal_candle_close_time": self.current_bar["timestamp"].isoformat(),
                "signal_reference_price": str(price),
                "confidence": str(kwargs.get("confidence", "1")),
                "stop_distance": str(stop_distance),
                "take_profit_distance": str(take_distance) if take_distance is not None else None,
                "max_entry_drift_bps": str(kwargs.get("max_entry_drift_bps", "20")),
                "expires_at": expires.isoformat(),
            }
        )


class _Context:
    def __init__(self, runtime: _Runtime):
        self.runtime = runtime
        self.params = dict(runtime.params)
        self.current_dt: datetime | None = None
        self.data = runtime.data

    def set_universe(self, *_args, **_kwargs):
        return None

    def subscribe(self, *_args, **_kwargs):
        return None

    def set_warmup(self, *_args, **_kwargs):
        return None

    def set_default_protection(self, **_kwargs):
        return None


def _validate_source(source: str) -> None:
    tree = ast.parse(source)
    if len(list(ast.walk(tree))) > 20_000:
        raise ValueError("strategy source is too complex")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("imports are not allowed in Shadow worker")
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in _DANGEROUS_NAMES):
            raise ValueError(f"unsafe name: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("dunder attributes are not allowed in Shadow worker")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DANGEROUS_NAMES:
                raise ValueError(f"unsafe call: {node.func.id}")
            if node.func.id in {"cancel_order"}:
                raise ValueError("order cancellation is not allowed in Shadow worker")


def _decode_bars(raw: object) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("bars_by_symbol must be a non-empty object")
    result: dict[str, list[dict[str, Any]]] = {}
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    for symbol, rows in raw.items():
        if not isinstance(rows, list) or not rows:
            raise ValueError("historical bars must be lists")
        decoded: list[dict[str, Any]] = []
        previous_timestamp: datetime | None = None
        for row in rows:
            if not isinstance(row, dict) or set(row) != required:
                raise ValueError("historical bar schema mismatch")
            timestamp = datetime.fromisoformat(str(row["timestamp"]))
            if timestamp.tzinfo is None:
                raise ValueError("historical timestamps must be timezone-aware")
            values = {field: float(str(row[field])) for field in required - {"timestamp"}}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError("historical bar values must be finite")
            if values["high"] < values["low"] or values["open"] < values["low"] or values["open"] > values["high"]:
                raise ValueError("historical bar OHLC values are inconsistent")
            if values["close"] < values["low"] or values["close"] > values["high"]:
                raise ValueError("historical bar OHLC values are inconsistent")
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError("historical bar timestamps must be strictly increasing")
            previous_timestamp = timestamp
            decoded.append(
                {
                    "timestamp": timestamp.astimezone(UTC),
                    **values,
                }
            )
        result[str(symbol)] = sorted(decoded, key=lambda item: item["timestamp"])
    return result


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _canonical_symbol(value: str) -> str:
    raw = value.strip().upper()
    if ":" in raw:
        prefix, suffix = raw.split(":", 1)
        raw = suffix if prefix == "CRYPTO" else prefix
    raw = raw.split("@", 1)[0]
    return raw if "/" in raw else f"{raw[:-4]}/USDT" if raw.endswith("USDT") else raw


def _order_direction(value: object) -> int:
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return 0
    return 1 if numeric > 0 else -1 if numeric < 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
