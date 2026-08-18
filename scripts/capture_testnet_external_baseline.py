"""Capture the current BTC/ETH Binance Testnet position baseline read-only."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_baseline_keys

BASELINE_FILENAME = "testnet-external-baseline.json"
ALLOWED_BASELINE_KEYS = execution_baseline_keys()
BASELINE_MATCHED = "BASELINE_MATCHED"
MANUAL_BASELINE_DRIFT = "MANUAL_BASELINE_DRIFT"
BASELINE_ACK_REQUIRED = "MANUAL_BASELINE_ACK_REQUIRED"
BASELINE_REFRESHED = "BASELINE_REFRESHED"


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


def persist_external_baseline(
    positions: dict[str, str],
    *,
    path: Path | None = None,
    audit: dict[str, Any] | None = None,
) -> Path:
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
    if audit:
        payload["last_acknowledgement"] = audit
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)
    return destination


def _baseline_fingerprint(positions: dict[str, str]) -> str:
    import hashlib

    encoded = json.dumps(positions, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_baseline_lifecycle(*, path: Path | None = None) -> dict[str, Any]:
    """Compare the durable manual baseline with the current exchange snapshot.

    This is read-only.  A drift is intentionally reported as an acknowledgement
    state; it never rewrites the baseline or creates a V2 position.
    """
    source = path or _baseline_path()
    try:
        persisted = load_persisted_external_baseline(path=source)
    except RuntimeError as exc:
        return {
            "status": "BASELINE_UNAVAILABLE",
            "acknowledgement_status": BASELINE_ACK_REQUIRED,
            "persisted": None,
            "observed": None,
            "drift_keys": [],
            "error": str(exc),
        }
    try:
        observed = capture_baseline()
    except RuntimeError as exc:
        return {
            "status": "BASELINE_UNAVAILABLE",
            "acknowledgement_status": BASELINE_ACK_REQUIRED,
            "persisted": persisted,
            "observed": None,
            "drift_keys": [],
            "error": str(exc),
        }
    drift_keys = sorted(set(persisted) | set(observed))
    drift_keys = [key for key in drift_keys if persisted.get(key) != observed.get(key)]
    matched = not drift_keys
    return {
        "status": BASELINE_MATCHED if matched else MANUAL_BASELINE_DRIFT,
        "acknowledgement_status": None if matched else BASELINE_ACK_REQUIRED,
        "persisted": persisted,
        "observed": observed,
        "drift_keys": drift_keys,
        "snapshot_fingerprint": _baseline_fingerprint(observed),
        "error": None,
    }


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


def require_persisted_external_baseline(
    *,
    path: Path | None = None,
    allow_manual_drift: bool = False,
) -> dict[str, str]:
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
        # A stale baseline that names a symbol/direction with an acknowledged
        # V2 intent is a projection-recovery problem, not permission to rewrite
        # operator-owned external exposure.  Keep the normal mismatch guard
        # fail-closed for all other baseline drift.
        try:
            from sqlalchemy import select

            from services.automated_trading.domain.enums import V2ExecutionMode
            from services.automated_trading.infrastructure.models import V2ExecutionIntent
            from services.database import get_session_factory

            with get_session_factory()() as session:
                acknowledged_keys = {
                    f"{intent.symbol}:{intent.direction}"
                    for intent in session.scalars(
                        select(V2ExecutionIntent).where(
                            V2ExecutionIntent.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                            V2ExecutionIntent.state == "EXCHANGE_ACKNOWLEDGED",
                        )
                    )
                }
            for key in sorted(set(persisted) & acknowledged_keys):
                raise RuntimeError(f"SYSTEM_POSITION_PROJECTION_GAP: {key}")
        except RuntimeError:
            raise
        except Exception:
            pass
        if allow_manual_drift:
            return persisted
        raise RuntimeError(
            "EXTERNAL_BASELINE_MISMATCH: "
            f"persisted={json.dumps(persisted, sort_keys=True)} "
            f"observed={json.dumps(observed, sort_keys=True)}"
        )
    return persisted


def acknowledge_baseline_refresh(
    *,
    operator: str,
    reason: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """Explicitly adopt the current Testnet manual exposure as the new baseline.

    The caller must provide an operator identity and reason.  The exchange is
    read only; only the durable baseline audit record is atomically replaced.
    """
    if not operator.strip() or not reason.strip():
        raise RuntimeError("BASELINE_ACKNOWLEDGEMENT_REQUIRES_OPERATOR_AND_REASON")
    source = path or _baseline_path()
    previous = load_persisted_external_baseline(path=source)
    observed = capture_baseline()
    acknowledged_at = datetime.now(UTC).isoformat()
    audit = {
        "status": BASELINE_REFRESHED,
        "previous_positions": previous,
        "new_positions": observed,
        "operator_acknowledgement": operator.strip(),
        "reason": reason.strip(),
        "acknowledged_at": acknowledged_at,
        "snapshot_fingerprint": _baseline_fingerprint(observed),
    }
    persist_external_baseline(observed, path=source, audit=audit)
    return audit


def capture_baseline(*, allow_system_projection_gaps: bool = False) -> dict[str, str]:
    from shared.config import settings

    if not settings.binance_use_testnet or settings.live_trading_enabled:
        raise RuntimeError("external baseline capture is Testnet-only")

    from sqlalchemy import select

    from services.automated_trading.domain.client_order_id import is_v2_client_order_id
    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
    from services.automated_trading.infrastructure.models import V2ExchangeOrder, V2ExecutionIntent, V2ManagedPosition
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
        unprojected_system_position_keys = {
            f"{intent.symbol}:{intent.direction}"
            for order, intent in session.execute(
                select(V2ExchangeOrder, V2ExecutionIntent)
                .join(V2ExecutionIntent, V2ExchangeOrder.intent_id == V2ExecutionIntent.intent_id)
                .where(
                    V2ExecutionIntent.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value,
                    V2ExecutionIntent.state == "EXCHANGE_ACKNOWLEDGED",
                )
            )
            if order.exchange_order_id and is_v2_client_order_id(order.client_order_id)
        }
    if unprojected_system_position_keys and not allow_system_projection_gaps:
        # An acknowledged V2 order is unresolved local execution fact even when
        # Binance is currently flat (for example, the order filled and was
        # subsequently closed before the projection was persisted).  Do not
        # rewrite the durable external baseline until the exact intent has been
        # reconciled to a terminal state.
        raise RuntimeError("SYSTEM_POSITION_PROJECTION_GAP: " + ",".join(sorted(unprojected_system_position_keys)))
    baseline: dict[str, str] = {}
    execution_symbols = set(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    for position in snapshot.positions:
        symbol = canonical_market_symbol(position.symbol)
        if symbol not in execution_symbols:
            continue
        quantity = Decimal(str(position.quantity))
        key = f"{symbol}:{position.direction}"
        # Recovery bootstrap may temporarily expose the authoritative quantity
        # as a baseline so the scheduler can run its exact-identity projection
        # repair.  The normal capture path remains fail-closed above.
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
    operations.add_argument("--inspect-lifecycle", action="store_true")
    operations.add_argument("--acknowledge-refresh", action="store_true")
    parser.add_argument("--operator", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--allow-manual-drift", action="store_true")
    parser.add_argument(
        "--bootstrap-projection-recovery",
        action="store_true",
        help="emit a temporary authoritative baseline while exact V2 projection gaps are repaired",
    )
    args = parser.parse_args()
    try:
        if args.inspect_lifecycle:
            result = inspect_baseline_lifecycle()
            baseline = result
        elif args.acknowledge_refresh:
            baseline = acknowledge_baseline_refresh(operator=args.operator, reason=args.reason)
        elif args.bootstrap_projection_recovery:
            baseline = capture_baseline(allow_system_projection_gaps=True)
        elif args.capture_persisted:
            baseline = capture_baseline()
            persist_external_baseline(baseline)
        elif args.require_persisted:
            baseline = require_persisted_external_baseline(allow_manual_drift=args.allow_manual_drift)
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
