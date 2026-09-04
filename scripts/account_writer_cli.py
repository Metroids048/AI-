"""Explicit operator workflow for binding a Binance account to one SQLite DB."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from services.automated_trading.infrastructure.account_writer import (
    OPERATOR_IDENTITY_ENV,
    AccountWriterFenceError,
    bind_account,
    database_identity,
    rebind_account,
    registry_path,
    resolve_account_scope,
)


def _operator_value(argument: str | None, environment_name: str) -> str:
    return (argument or os.environ.get(environment_name, "")).strip()


def _database_recovery_clear(database_url: str) -> bool:
    from sqlalchemy import select

    from scripts.prepare_database import prepare_database
    from services.automated_trading.infrastructure.models import V2ExecutionIntent, V2ManagedPosition
    from services.database import get_session_factory

    prepare_database(database_url)
    with get_session_factory(database_url)() as session:
        active_intents = session.scalars(
            select(V2ExecutionIntent.intent_id).where(
                V2ExecutionIntent.state.in_(("INTENT_CREATED", "EXCHANGE_SUBMITTING", "EXCHANGE_UNKNOWN"))
            )
        )
        if next(iter(active_intents), None) is not None:
            return False
        active_positions = session.scalars(
            select(V2ManagedPosition.position_id).where(V2ManagedPosition.state.not_in(("CLOSED", "QUARANTINED")))
        )
        return next(iter(active_positions), None) is None


def _exchange_preflight() -> tuple[bool, bool]:
    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    snapshot = adapter.fetch_authoritative_snapshot()
    return (
        not any(position.quantity > 0 for position in snapshot.positions),
        not snapshot.pending_orders,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("bind-account-writer", "rebind-account-writer"):
        command = subparsers.add_parser(name)
        command.add_argument("--account-scope-key", required=True)
        command.add_argument("--database-url", required=True)
        command.add_argument("--operator-identity")
        command.add_argument("--operator-reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        scope = resolve_account_scope(account_identity=args.account_scope_key.rsplit(":", 1)[-1])
        if scope.key != args.account_scope_key:
            raise AccountWriterFenceError("ACCOUNT_SCOPE_KEY_INVALID")
        database_id = database_identity(args.database_url)
        operator_identity = _operator_value(args.operator_identity, OPERATOR_IDENTITY_ENV)
        if args.command == "bind-account-writer":
            binding: dict[str, Any] = bind_account(
                account_scope_key=scope.key,
                database_id=database_id,
                operator_identity=operator_identity,
                operator_reason=args.operator_reason,
            )
        else:
            flat, orders_empty = _exchange_preflight()
            binding = rebind_account(
                account_scope_key=scope.key,
                database_id=database_id,
                operator_identity=operator_identity,
                operator_reason=args.operator_reason,
                exchange_is_flat=lambda: flat,
                exchange_open_orders_empty=lambda: orders_empty,
                new_database_recovery_clear=lambda: _database_recovery_clear(args.database_url),
            )
        print(json.dumps({"status": "BOUND", "registry": str(registry_path()), **binding}, sort_keys=True))
        return 0
    except AccountWriterFenceError as exc:
        print(json.dumps({"status": "REJECTED", "reason": exc.code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
