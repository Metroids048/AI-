"""Capture the current BTC/ETH Binance Testnet position baseline read-only."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_baseline_keys

BASELINE_FILENAME = "testnet-external-baseline.json"
ALLOWED_BASELINE_KEYS = execution_baseline_keys()


def _baseline_path() -> Path:
    configured = os.getenv("V2_EXTERNAL_BASELINE_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / ".local" / BASELINE_FILENAME


def _validated_positions(payload: object) -> dict[str, str]:
    # An empty mapping is a successful capture that found no unmanaged exposure.
    # Only file absence proves "never captured"; see require_persisted_external_baseline.
    if not isinstance(payload, dict):
        raise RuntimeError("EXTERNAL_BASELINE_EMPTY_OR_INVALID")
    positions: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or key not in ALLOWED_BASELINE_KEYS:
            raise RuntimeError(f"EXTERNAL_BASELINE_INVALID_KEY: {key}")
        quantity = Decimal(str(value))
        if quantity <= 0:
            raise RuntimeError(f"EXTERNAL_BASELINE_INVALID_QUANTITY: {key}")
        positions[key] = str(quantity)
    return positions


def persist_external_baseline(positions: dict[str, str], *, path: Path | None = None) -> Path:
    """Persist an explicitly captured Testnet baseline outside ephemeral scheduler state."""
    destination = path or _baseline_path()
    normalized = _validated_positions(positions)
    payload = {
        "schema_version": 1,
        "execution_mode": "BINANCE_TESTNET",
        "captured_symbols": list(AUTO_SIMULATION_EXECUTION_SYMBOLS),
        "captured_at": datetime.now(UTC).isoformat(),
        "source": "binance_testnet_authoritative_snapshot",
        "positions": normalized,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_persisted_external_baseline(*, path: Path | None = None) -> dict[str, str]:
    """Load the operator's durable Testnet baseline without consulting process state."""
    source = path or _baseline_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"EXTERNAL_BASELINE_NOT_PERSISTED: {source}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"EXTERNAL_BASELINE_INVALID_PERSISTED_JSON: {source}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("execution_mode") != "BINANCE_TESTNET"
        or tuple(payload.get("captured_symbols", ())) != AUTO_SIMULATION_EXECUTION_SYMBOLS
        or payload.get("source") != "binance_testnet_authoritative_snapshot"
    ):
        raise RuntimeError(f"EXTERNAL_BASELINE_INVALID_PERSISTED_RECORD: {source}")
    return _validated_positions(payload.get("positions"))


def require_persisted_external_baseline(*, path: Path | None = None) -> dict[str, str]:
    """Fail closed unless the current unmanaged exposure matches a saved baseline."""
    source = path or _baseline_path()
    observed = capture_baseline()
    try:
        persisted = load_persisted_external_baseline(path=source)
    except RuntimeError as exc:
        if observed:
            raise RuntimeError(
                f"EXTERNAL_BASELINE_NOT_PERSISTED: observed unmanaged exposure {json.dumps(observed, sort_keys=True)}"
            ) from exc
        raise
    if observed != persisted:
        raise RuntimeError(
            "EXTERNAL_BASELINE_MISMATCH: "
            f"persisted={json.dumps(persisted, sort_keys=True)} "
            f"observed={json.dumps(observed, sort_keys=True)}"
        )
    return persisted


def capture_baseline() -> dict[str, str]:
    from shared.config import settings

    if not settings.binance_use_testnet or settings.live_trading_enabled:
        raise RuntimeError("external baseline capture is Testnet-only")

    from sqlalchemy import select

    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
    from services.automated_trading.infrastructure.models import V2ManagedPosition
    from services.data.universe import canonical_market_symbol
    from services.database import get_session_factory

    snapshot = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET).fetch_authoritative_snapshot()
    with get_session_factory()() as session:
        managed = {
            f"{position.symbol}:{position.direction}": Decimal(str(position.quantity))
            for position in session.scalars(
                select(V2ManagedPosition).where(
                    V2ManagedPosition.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                    V2ManagedPosition.state.not_in(("CLOSED", "QUARANTINED")),
                )
            )
        }
    baseline: dict[str, str] = {}
    execution_symbols = set(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    for position in snapshot.positions:
        symbol = canonical_market_symbol(position.symbol)
        if symbol not in execution_symbols:
            continue
        quantity = Decimal(str(position.quantity))
        key = f"{symbol}:{position.direction}"
        external_quantity = quantity - managed.get(key, Decimal("0"))
        if external_quantity > 0:
            baseline[key] = str(external_quantity)
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", dest="as_json")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--capture-persisted", action="store_true")
    operations.add_argument("--require-persisted", action="store_true")
    args = parser.parse_args()
    try:
        if args.capture_persisted:
            baseline = capture_baseline()
            persist_external_baseline(baseline)
        elif args.require_persisted:
            baseline = require_persisted_external_baseline()
        else:
            baseline = capture_baseline()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(baseline, separators=(",", ":")))
    else:
        print(json.dumps(baseline, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
