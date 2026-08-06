#!/usr/bin/env python3
"""Read-only proof for the legacy writer while V2 runs in shadow mode.

This verifier never calls an execution service and never mutates the database
or Binance.  It observes the ordinary ``paper_runtime_cycle`` and may emit
only ``LEGACY_NATURAL_SCHEDULER_TESTNET`` evidence after a real, natural
legacy entry and exit have both been reconciled to Binance Testnet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_URL = f"sqlite:///{(ROOT / '.local_paper_console.db').as_posix()}"
DEFAULT_SCHEDULER_STATE = ROOT / "logs" / "scheduler-state.json"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts"
EVIDENCE_DIR = ROOT / "docs" / "evidence" / "automated_trading_legacy"
PROOF_TYPE = "LEGACY_NATURAL_SCHEDULER_TESTNET"
FORBIDDEN_ORIGINS = frozenset(
    {
        "acceptance",
        "manual",
        "canary",
        "testnet_contract",
        "arm_validated_testnet_execution",
        "recovery_manual",
        "synthetic",
        "emulator",
        "fake",
    }
)
NATURAL_ORIGINS = frozenset({"live_scheduler", "paper_scheduler"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _contains_forbidden(*values: Any) -> bool:
    return any(marker in str(value or "").lower() for marker in FORBIDDEN_ORIGINS for value in values)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def scheduler_lane_blockers(state: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    """Require the exact legacy writer job to be alive and recently successful."""
    blockers: list[str] = []
    if not state.get("running"):
        return ["scheduler_offline"]
    counts = state.get("task_run_counts") or {}
    if int(counts.get("paper_runtime_cycle") or 0) <= 0:
        blockers.append("paper_runtime_cycle_not_running")
    result = (state.get("task_last_results") or {}).get("paper_runtime_cycle")
    if not isinstance(result, dict) or result.get("status") == "error":
        blockers.append("paper_runtime_cycle_last_result_unhealthy")
    last_cycle = _parse_time(state.get("last_auto_cycle_at"))
    reference = now or _utcnow()
    if last_cycle is None or (reference - last_cycle).total_seconds() > 600:
        blockers.append("paper_runtime_cycle_stale")
    return blockers


def shadow_runtime_observed(*, settings_engine: str, scheduler_state: dict[str, Any]) -> bool:
    """The launcher env belongs to its child process; scheduler state is its durable witness."""
    if settings_engine.lower() == "v2_shadow":
        return True
    result = (scheduler_state.get("task_last_results") or {}).get("automated_trading_v2_cycle")
    return (
        isinstance(result, dict)
        and result.get("status") == "completed"
        and str(result.get("v2_activation", "")).upper() == "SHADOW"
    )


@dataclass
class ExchangeView:
    positions: dict[str, str] = field(default_factory=dict)
    open_order_ids: dict[str, set[str]] = field(default_factory=dict)
    complete: bool = True
    error: str | None = None

    def is_flat_and_clean(self, symbol: str) -> bool:
        return (
            self.complete
            and symbol in self.positions
            and _decimal(self.positions.get(symbol)) == 0
            and not self.open_order_ids.get(symbol, set())
        )


@dataclass
class ObservationSnapshot:
    scheduler_state: dict[str, Any]
    paper_runs: list[dict[str, Any]]
    configs: dict[str, dict[str, Any]]
    orders: list[dict[str, Any]]
    exchange_orders: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    position_snapshots: list[dict[str, Any]]
    protections: list[dict[str, Any]]
    exchange: ExchangeView
    v2_exchange_order_ids: set[str] = field(default_factory=set)


class ReadOnlySource(Protocol):
    """The narrow, read-only boundary used by the state machine and tests."""

    def preflight(self) -> list[str]: ...

    def snapshot(self, symbols: tuple[str, ...]) -> ObservationSnapshot: ...

    def confirm_exchange_order(self, *, symbol: str, exchange_order_id: str) -> dict[str, Any] | None: ...


@dataclass
class LegacyNaturalEvidence:
    proof_type: str = PROOF_TYPE
    natural_strategy: bool = True
    engine: str = "v2_shadow"
    writer: str = "legacy"
    cycle_source: str = "paper_runtime_cycle"
    proof_session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    git_commit: str = ""
    status: str = "WAITING_FOR_NATURAL_ENTRY"
    started_at: str = ""
    completed_at: str | None = None
    symbol: str | None = None
    paper_run_id: str | None = None
    config_snapshot_id: str | None = None
    config_snapshot_hash: str | None = None
    entry_order_execution_id: str | None = None
    entry_exchange_order_id: str | None = None
    entry_trade_ids: list[str] = field(default_factory=list)
    entry_filled_quantity: str | None = None
    entry_average_price: str | None = None
    entry_direction: str | None = None
    requested_leverage: str | None = None
    requested_notional: str | None = None
    position_record_id: str | None = None
    protection_exchange_order_ids: list[str] = field(default_factory=list)
    exit_reason: str | None = None
    exit_observed_at: str | None = None
    exit_order_execution_id: str | None = None
    exit_exchange_order_id: str | None = None
    exit_trade_ids: list[str] = field(default_factory=list)
    final_exchange_position_qty: str | None = None
    final_exchange_open_orders: list[str] = field(default_factory=list)
    final_local_position_status: str | None = None
    final_reconciliation_status: str | None = None
    v2_shadow_network_orders: int = 0
    overall_passed: bool = False
    baseline: dict[str, Any] = field(default_factory=dict)
    timeline: list[dict[str, str]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def transition(self, status: str, detail: str) -> None:
        self.status = status
        self.timeline.append({"at": _utcnow().isoformat(), "status": status, "detail": detail})

    @property
    def passed(self) -> bool:
        return self.overall_passed and self.status == "PASSED"


class LegacyNaturalObserver:
    """Strict state machine; it owns proof state only, never trading state."""

    def __init__(
        self, source: ReadOnlySource, *, symbols: tuple[str, ...], evidence: LegacyNaturalEvidence | None = None
    ) -> None:
        self.source = source
        self.symbols = symbols
        self.evidence = evidence or LegacyNaturalEvidence(git_commit=_git_commit(), started_at=_utcnow().isoformat())

    def start(self) -> LegacyNaturalEvidence:
        blockers = self.source.preflight()
        if blockers:
            self.evidence.blockers = blockers
            self.evidence.transition("BLOCKED_PREFLIGHT", "; ".join(blockers))
            self.evidence.completed_at = _utcnow().isoformat()
            return self.evidence

        snapshot = self.source.snapshot(self.symbols)
        if not snapshot.exchange.complete:
            return self._fail(
                "BLOCKED_PREFLIGHT", f"exchange baseline unavailable: {snapshot.exchange.error or 'unknown'}"
            )
        if snapshot.v2_exchange_order_ids:
            return self._fail("FAILED_SAFETY_VIOLATION", "V2 shadow has existing exchange order records")
        selected = [
            symbol
            for symbol in self.symbols
            if self._clean_baseline(snapshot, symbol) and self._armed_symbol(snapshot, symbol)
        ]
        if not selected:
            self.evidence.transition(
                "BLOCKED_UNCLEAN_BASELINE", "no symbol has zero exchange/local position and zero open orders"
            )
            self.evidence.completed_at = _utcnow().isoformat()
            return self.evidence
        baseline_at = _utcnow()
        self.evidence.started_at = baseline_at.isoformat()
        self.evidence.baseline = {
            "captured_at": baseline_at.isoformat(),
            "selected_symbols": selected,
            "order_ids": sorted(
                str(row.get("order_execution_id")) for row in snapshot.orders if row.get("order_execution_id")
            ),
            "exchange_order_ids": sorted(
                str(row.get("exchange_order_record_id"))
                for row in snapshot.exchange_orders
                if row.get("exchange_order_record_id")
            ),
            "fill_ids": sorted(str(row.get("receipt_id")) for row in snapshot.fills if row.get("receipt_id")),
            "active_runs": [str(row.get("paper_run_id")) for row in snapshot.paper_runs],
            "active_configs": {
                str(run_id): {
                    "config_snapshot_id": str(config.get("config_snapshot_id") or ""),
                    "config_hash": str(config.get("config_hash") or ""),
                }
                for run_id, config in snapshot.configs.items()
            },
            "v2_exchange_order_ids": sorted(snapshot.v2_exchange_order_ids),
            "scheduler_state": snapshot.scheduler_state,
        }
        self.evidence.transition("WAITING_FOR_NATURAL_ENTRY", f"clean symbols: {', '.join(selected)}")
        return self.evidence

    def resume(self) -> LegacyNaturalEvidence:
        if self.evidence.git_commit != _git_commit():
            self.evidence.transition("FAILED_PROVENANCE_CONTAMINATED", "git commit changed after proof session started")
            self.evidence.completed_at = _utcnow().isoformat()
            return self.evidence
        blockers = self.source.preflight()
        if blockers:
            return self._fail("BLOCKED_PREFLIGHT", "; ".join(blockers))
        snapshot = self.source.snapshot(self.symbols)
        if not snapshot.exchange.complete:
            return self._fail("FAILED_INCONSISTENT_STATE", "exchange resume snapshot unavailable")
        if snapshot.v2_exchange_order_ids:
            return self._fail("FAILED_SAFETY_VIOLATION", "V2 shadow has exchange order records")
        if self._v2_network_submit(snapshot):
            return self._fail("FAILED_SAFETY_VIOLATION", "V2 shadow recorded an exchange order")
        if self.evidence.paper_run_id:
            run = self._run(snapshot, self.evidence.paper_run_id)
            config = snapshot.configs.get(self.evidence.paper_run_id)
            if run is None or config is None or config.get("config_hash") != self.evidence.config_snapshot_hash:
                return self._fail("FAILED_PROVENANCE_CONTAMINATED", "active run or config snapshot changed")
            if (
                config.get("config_snapshot_id") != self.evidence.config_snapshot_id
                or run.get("paper_status") != "running"
            ):
                return self._fail("FAILED_PROVENANCE_CONTAMINATED", "active run/config identity is no longer stable")
        if self._foreign_activity(snapshot):
            return self._fail("FAILED_PROVENANCE_CONTAMINATED", "resume found post-baseline foreign activity")
        return self.evidence

    def poll(self) -> LegacyNaturalEvidence:
        if self.evidence.status.startswith(("PASSED", "BLOCKED", "FAILED")):
            return self.evidence
        snapshot = self.source.snapshot(self.symbols)
        if self._v2_network_submit(snapshot):
            return self._fail("FAILED_SAFETY_VIOLATION", "V2 shadow recorded an exchange order")
        if any(
            row.get("symbol") == self.evidence.symbol and row.get("management_status") == "UNMANAGED_EXTERNAL_POSITION"
            for row in snapshot.positions
        ):
            return self._fail("FAILED_PROVENANCE_CONTAMINATED", "an external/manual position appeared during the proof")
        if self._foreign_activity(snapshot):
            return self._fail("FAILED_PROVENANCE_CONTAMINATED", "post-baseline order is not a legacy scheduler order")

        if self.evidence.entry_exchange_order_id is None:
            self._observe_entry(snapshot)
        elif not self.evidence.protection_exchange_order_ids:
            self._observe_protection(snapshot)
        elif self.evidence.exit_exchange_order_id is None:
            self._observe_exit(snapshot)
        else:
            self._finish(snapshot)
        return self.evidence

    def finish_timeout(self) -> LegacyNaturalEvidence:
        if self.evidence.entry_exchange_order_id is None:
            self.evidence.transition(
                "NO_NATURAL_ENTRY_OBSERVED", "entry timeout elapsed without a natural scheduler entry"
            )
        elif self.evidence.exit_exchange_order_id is None:
            self.evidence.transition(
                "ENTRY_OBSERVED_EXIT_PENDING", "entry is retained; resume this proof session to observe exit"
            )
        else:
            self.evidence.transition("TIMEOUT_INCONCLUSIVE", "exit observed but final reconciliation did not complete")
        self.evidence.completed_at = _utcnow().isoformat()
        return self.evidence

    def _clean_baseline(self, snapshot: ObservationSnapshot, symbol: str) -> bool:
        active_local = any(
            row.get("symbol") == symbol and row.get("management_status") not in {"CLOSED", "RECONCILED_GHOST"}
            for row in snapshot.positions
        )
        return not active_local and snapshot.exchange.is_flat_and_clean(symbol)

    def _armed_symbol(self, snapshot: ObservationSnapshot, symbol: str) -> bool:
        return any(
            symbol in (run.get("candidate_symbols") or run.get("symbol_scope") or [])
            and (run.get("execution_profile") or {}).get("execution_mode") == "binance_testnet"
            and (run.get("execution_profile") or {}).get("mirror_to_gateway") is True
            for run in snapshot.paper_runs
        )

    def _run(self, snapshot: ObservationSnapshot, run_id: str) -> dict[str, Any] | None:
        return next((run for run in snapshot.paper_runs if run.get("paper_run_id") == run_id), None)

    def _started_at(self) -> datetime:
        return _parse_time(self.evidence.started_at) or _utcnow()

    def _v2_network_submit(self, snapshot: ObservationSnapshot) -> bool:
        baseline = set(self.evidence.baseline.get("v2_exchange_order_ids") or [])
        current = set(snapshot.v2_exchange_order_ids)
        self.evidence.v2_shadow_network_orders = len(current - baseline)
        return bool(current - baseline)

    def _foreign_activity(self, snapshot: ObservationSnapshot) -> bool:
        start = self._started_at()
        baseline_ids = set(self.evidence.baseline.get("order_ids") or [])
        for order in snapshot.orders:
            created_at = _parse_time(order.get("created_at"))
            if (
                order.get("order_execution_id") in baseline_ids
                or created_at is None
                or created_at < start
                or order.get("symbol") not in self.symbols
            ):
                continue
            if self.evidence.entry_order_execution_id == order.get("order_execution_id"):
                continue
            if self.evidence.exit_order_execution_id == order.get("order_execution_id"):
                continue
            if not self._is_natural_scheduler_order(order):
                return True
        return False

    def _is_natural_scheduler_order(self, order: dict[str, Any]) -> bool:
        return (
            order.get("order_origin") in NATURAL_ORIGINS
            and order.get("cycle_source") == "runtime_scheduler"
            and not _contains_forbidden(
                order.get("order_origin"), order.get("cycle_source"), order.get("strategy_id"), order.get("test_run_id")
            )
        )

    def _observe_entry(self, snapshot: ObservationSnapshot) -> None:
        start = self._started_at()
        baseline_ids = set(self.evidence.baseline.get("order_ids") or [])
        for order in snapshot.orders:
            created_at = _parse_time(order.get("created_at"))
            if (
                bool(order.get("close_only_mode"))
                or order.get("order_execution_id") in baseline_ids
                or created_at is None
                or created_at < start
            ):
                continue
            if order.get("symbol") not in self.evidence.baseline.get("selected_symbols", []):
                continue
            if order.get("execution_status") != "filled" or not self._is_natural_scheduler_order(order):
                continue
            run_id = str(order.get("paper_run_id") or "")
            config = snapshot.configs.get(run_id)
            if not run_id or config is None:
                continue
            if order.get("config_snapshot_id") != config.get("config_snapshot_id") or order.get(
                "config_hash"
            ) != config.get("config_hash"):
                return self._fail("FAILED_INCONSISTENT_STATE", "entry order does not use the active immutable config")
            exchange_order_id = str(order.get("gateway_order_id") or "")
            if not exchange_order_id:
                return self._fail("FAILED_INCONSISTENT_STATE", "filled local entry has no exchange order id")
            confirmation = self.source.confirm_exchange_order(
                symbol=str(order["symbol"]), exchange_order_id=exchange_order_id
            )
            if not confirmation or not confirmation.get("trade_ids"):
                return self._fail("FAILED_INCONSISTENT_STATE", "Binance does not confirm entry order and fills")
            local_fills = [fill for fill in snapshot.fills if fill.get("exchange_order_id") == exchange_order_id]
            if not local_fills:
                return self._fail("FAILED_INCONSISTENT_STATE", "entry has no local exchange fill receipt")
            fill = local_fills[-1]
            filled_quantity = _decimal(fill.get("filled_quantity") or fill.get("cumulative_filled_quantity"))
            if filled_quantity <= 0 or _decimal(confirmation.get("filled_quantity")) != filled_quantity:
                return self._fail("FAILED_INCONSISTENT_STATE", "local and Binance entry fill quantities disagree")
            position = next(
                (
                    row
                    for row in snapshot.positions
                    if row.get("entry_order_id") == order.get("order_execution_id")
                    and row.get("management_status") == "MANAGED_STRATEGY"
                ),
                None,
            )
            if position is None or _decimal(position.get("quantity")) != filled_quantity:
                return self._fail(
                    "FAILED_INCONSISTENT_STATE", "entry fill does not project one matching managed position"
                )
            if _decimal(snapshot.exchange.positions.get(str(order["symbol"]))) == 0:
                return self._fail("FAILED_INCONSISTENT_STATE", "Binance is flat after a purported entry fill")
            context = order.get("entry_context") or {}
            profile = (config.get("config") or {}).get("execution_profile") or config.get("execution_profile") or {}
            for field_name in ("requested_leverage", "requested_notional"):
                requested = _decimal(context.get(field_name))
                configured = _decimal(profile.get(field_name))
                if requested > 0 and configured > 0 and requested != configured:
                    return self._fail(
                        "FAILED_INCONSISTENT_STATE", f"entry {field_name} differs from active operator profile"
                    )
            self.evidence.symbol = str(order["symbol"])
            self.evidence.paper_run_id = run_id
            self.evidence.config_snapshot_id = str(config.get("config_snapshot_id") or "")
            self.evidence.config_snapshot_hash = str(config.get("config_hash") or "")
            self.evidence.entry_order_execution_id = str(order.get("order_execution_id") or "")
            self.evidence.entry_exchange_order_id = exchange_order_id
            self.evidence.entry_trade_ids = [str(item) for item in confirmation["trade_ids"]]
            self.evidence.entry_filled_quantity = str(filled_quantity)
            self.evidence.entry_average_price = str(
                confirmation.get("average_fill_price") or fill.get("average_fill_price")
            )
            self.evidence.requested_leverage = str(context.get("requested_leverage") or context.get("leverage") or "")
            self.evidence.requested_notional = str(context.get("requested_notional") or "")
            self.evidence.position_record_id = str(position.get("position_record_id") or "")
            self.evidence.entry_direction = str(position.get("position_side") or order.get("direction") or "")
            self.evidence.transition(
                "ENTRY_FILLED", f"natural {self.evidence.symbol} entry {exchange_order_id} confirmed"
            )
            self.evidence.transition("POSITION_OPEN", "matching managed local position and Binance position observed")
            return

    def _observe_protection(self, snapshot: ObservationSnapshot) -> None:
        protection = next(
            (
                row
                for row in snapshot.protections
                if row.get("position_record_id") == self.evidence.position_record_id and row.get("status") == "ACTIVE"
            ),
            None,
        )
        if protection is None:
            if _decimal(snapshot.exchange.positions.get(self.evidence.symbol or "")) == 0:
                return self._fail(
                    "FAILED_INCONSISTENT_STATE", "entry position disappeared before protection became active"
                )
            return
        ids = [
            str(value)
            for value in (protection.get("stop_exchange_order_id"), protection.get("take_profit_exchange_order_id"))
            if value
        ]
        open_ids = snapshot.exchange.open_order_ids.get(self.evidence.symbol or "", set())
        if len(ids) != 2 or len(set(ids)) != 2 or not set(ids).issubset(open_ids):
            return self._fail(
                "FAILED_INCONSISTENT_STATE", "active local protection is missing from Binance open orders"
            )
        for order_id in ids:
            confirmation = self.source.confirm_exchange_order(
                symbol=self.evidence.symbol or "", exchange_order_id=order_id
            )
            if (
                not confirmation
                or not confirmation.get("exists")
                or confirmation.get("status") in {"closed", "canceled", "cancelled"}
            ):
                return self._fail("FAILED_INCONSISTENT_STATE", f"Binance protection order {order_id} is not active")
            if confirmation.get("quantity") and _decimal(confirmation["quantity"]) != _decimal(
                self.evidence.entry_filled_quantity
            ):
                return self._fail(
                    "FAILED_INCONSISTENT_STATE", f"Binance protection order {order_id} quantity differs from position"
                )
            expected_side = "sell" if self.evidence.entry_direction.lower() in {"long", "buy"} else "buy"
            if confirmation.get("side") and str(confirmation["side"]).lower() != expected_side:
                return self._fail("FAILED_INCONSISTENT_STATE", f"Binance protection order {order_id} side is unsafe")
        self.evidence.protection_exchange_order_ids = ids
        self.evidence.transition("PROTECTION_ACTIVE", f"Binance protection active: {', '.join(ids)}")
        self.evidence.transition("WAITING_FOR_NATURAL_EXIT", "waiting for an existing automatic exit mechanism")

    def _observe_exit(self, snapshot: ObservationSnapshot) -> None:
        start = self._started_at()
        baseline_ids = set(self.evidence.baseline.get("order_ids") or [])
        for order in snapshot.orders:
            created_at = _parse_time(order.get("created_at"))
            if (
                not bool(order.get("close_only_mode"))
                or order.get("order_execution_id") in baseline_ids
                or created_at is None
                or created_at < start
            ):
                continue
            if order.get("paper_run_id") != self.evidence.paper_run_id or order.get("symbol") != self.evidence.symbol:
                continue
            if (
                order.get("config_snapshot_id") != self.evidence.config_snapshot_id
                or order.get("config_hash") != self.evidence.config_snapshot_hash
            ):
                return self._fail("FAILED_PROVENANCE_CONTAMINATED", "exit order changed immutable config identity")
            if order.get("execution_status") != "filled" or not self._is_natural_scheduler_order(order):
                continue
            if _contains_forbidden(
                order.get("rejection_reason"), order.get("entry_context"), order.get("lifecycle_history")
            ):
                return self._fail("FAILED_PROVENANCE_CONTAMINATED", "forbidden marker found in exit provenance")
            exchange_order = next(
                (
                    row
                    for row in snapshot.exchange_orders
                    if row.get("local_order_execution_id") == order.get("order_execution_id")
                    and row.get("reduce_only") is True
                ),
                None,
            )
            exchange_order_id = str(
                (exchange_order or {}).get("exchange_order_id") or order.get("gateway_order_id") or ""
            )
            if not exchange_order_id:
                return self._fail("FAILED_INCONSISTENT_STATE", "natural exit lacks a reduce-only Binance order id")
            confirmation = self.source.confirm_exchange_order(
                symbol=str(order["symbol"]), exchange_order_id=exchange_order_id
            )
            if (
                not confirmation
                or not confirmation.get("trade_ids")
                or not confirmation.get("reduce_only")
                or _decimal(confirmation.get("filled_quantity")) != _decimal(self.evidence.entry_filled_quantity)
            ):
                return self._fail("FAILED_INCONSISTENT_STATE", "Binance does not confirm a reduce-only exit fill")
            local_fills = [fill for fill in snapshot.fills if fill.get("exchange_order_id") == exchange_order_id]
            if not local_fills or _decimal(
                local_fills[-1].get("filled_quantity") or local_fills[-1].get("cumulative_filled_quantity")
            ) != _decimal(self.evidence.entry_filled_quantity):
                return self._fail("FAILED_INCONSISTENT_STATE", "exit lacks a matching local fill receipt")
            self.evidence.exit_order_execution_id = str(order.get("order_execution_id") or "")
            self.evidence.exit_exchange_order_id = exchange_order_id
            self.evidence.exit_trade_ids = [str(item) for item in confirmation["trade_ids"]]
            self.evidence.exit_observed_at = _utcnow().isoformat()
            context = order.get("entry_context") or {}
            self.evidence.exit_reason = str(
                context.get("exit_reason") or order.get("rejection_reason") or "automatic_exit"
            )
            self.evidence.transition("EXIT_FILLED", f"natural reduce-only exit {exchange_order_id} confirmed")
            self.evidence.transition("POSITION_CLOSED", "validating final exchange and local state")
            return

    def _finish(self, snapshot: ObservationSnapshot) -> None:
        symbol = self.evidence.symbol or ""
        qty = _decimal(snapshot.exchange.positions.get(symbol))
        open_orders = sorted(snapshot.exchange.open_order_ids.get(symbol, set()))
        self.evidence.final_exchange_position_qty = str(qty)
        self.evidence.final_exchange_open_orders = open_orders
        local_closed = any(
            row.get("position_record_id") == self.evidence.position_record_id
            and row.get("management_status") == "CLOSED"
            for row in snapshot.positions
        )
        snapshots = [
            row
            for row in snapshot.position_snapshots
            if row.get("position_record_id") == self.evidence.position_record_id
        ]
        latest_snapshot = max(
            snapshots,
            key=lambda row: _parse_time(row.get("snapshot_time")) or datetime.min.replace(tzinfo=UTC),
            default=None,
        )
        exit_at = _parse_time(self.evidence.exit_observed_at) or _utcnow()
        latest_zero = (
            latest_snapshot is not None
            and (_parse_time(latest_snapshot.get("snapshot_time")) or datetime.min.replace(tzinfo=UTC)) >= exit_at
            and _decimal(latest_snapshot.get("quantity")) == 0
        )
        self.evidence.final_local_position_status = "CLOSED" if local_closed and latest_zero else "OPEN_OR_INCONSISTENT"
        self.evidence.final_reconciliation_status = (
            "HEALTHY"
            if qty == 0 and not open_orders and self.evidence.final_local_position_status == "CLOSED"
            else "DEGRADED"
        )
        if self.evidence.final_reconciliation_status != "HEALTHY":
            return self._fail("FAILED_INCONSISTENT_STATE", "final local/exchange reconciliation is not healthy")
        self.evidence.overall_passed = True
        self.evidence.transition("RECONCILED", "Binance flat/clean and local position closed")
        self.evidence.transition("PASSED", "legacy natural Scheduler Testnet lifecycle proven")
        self.evidence.completed_at = _utcnow().isoformat()

    def _fail(self, status: str, detail: str) -> LegacyNaturalEvidence:
        self.evidence.transition(status, detail)
        self.evidence.completed_at = _utcnow().isoformat()
        return self.evidence


class LiveLegacySource:
    """Production read adapter. Its public surface intentionally has no writes."""

    def __init__(self, *, database_url: str, scheduler_state_path: Path) -> None:
        self.database_url = database_url
        self.scheduler_state_path = scheduler_state_path
        self._gateway: Any | None = None

    def preflight(self) -> list[str]:
        from scripts.check_execution_blockers import (
            _check_db_blockers,
            _check_market_data_blockers,
            _check_scheduler_blockers,
        )
        from services.automated_trading.infrastructure.runtime_lock import resolve_engine_activation
        from services.execution.v2_scheduler_entry import resolve_scheduler_v2_jobs
        from shared.config import settings

        blockers: list[str] = []
        state = self._scheduler_state()
        if not shadow_runtime_observed(settings_engine=settings.automated_trading_engine, scheduler_state=state):
            blockers.append(
                f"AUTOMATED_TRADING_ENGINE={settings.automated_trading_engine}; requires observed v2_shadow runtime"
            )
        activation = resolve_engine_activation(settings)
        if not activation.allow_legacy_writer or "paper_runtime_cycle" not in resolve_scheduler_v2_jobs(activation):
            blockers.append("legacy writer is not enabled")
        if not settings.binance_use_testnet or settings.live_trading_enabled:
            blockers.append("testnet safety boundary is not active")
        if not settings.binance_auto_execute:
            blockers.append("BINANCE_AUTO_EXECUTE is false")
        if not (settings.binance_api_key and settings.binance_api_secret):
            blockers.append("Binance credentials are not configured")
        from services.execution.gateway import BinanceUsdtPerpetualGateway

        if not BinanceUsdtPerpetualGateway.capability.supports_order_submit:
            blockers.append("Binance gateway does not support order submission")
        blockers.extend(_check_scheduler_blockers(state))
        blockers.extend(scheduler_lane_blockers(state))
        blockers.extend(_check_market_data_blockers(state))
        if self.database_url.startswith("sqlite:///"):
            blockers.extend(_check_db_blockers(Path(self.database_url.removeprefix("sqlite:///"))))
        else:
            blockers.append("legacy natural verifier currently requires an explicit SQLite database URL")
        return blockers

    def snapshot(self, symbols: tuple[str, ...]) -> ObservationSnapshot:
        from services.automated_trading.infrastructure import models as v2_models
        from services.database import get_session_factory
        from services.strategy_library import models
        from services.strategy_library.repository import ConfigSnapshotRepository

        with get_session_factory(self.database_url)() as session:
            runs = [
                self._row(row)
                for row in session.query(models.PaperRun).filter(models.PaperRun.paper_status == "running").all()
            ]
            configs = {
                str(run["paper_run_id"]): self._model(
                    ConfigSnapshotRepository(session).get_active(str(run["paper_run_id"]))
                )
                for run in runs
            }
            configs = {key: value for key, value in configs.items() if value}
            return ObservationSnapshot(
                scheduler_state=self._scheduler_state(),
                paper_runs=runs,
                configs=configs,
                orders=[self._row(row) for row in session.query(models.OrderExecution).all()],
                exchange_orders=[self._row(row) for row in session.query(models.ExchangeOrderRecord).all()],
                fills=[self._row(row) for row in session.query(models.ExchangeFillReceipt).all()],
                positions=[self._row(row) for row in session.query(models.PositionRecord).all()],
                position_snapshots=[self._row(row) for row in session.query(models.PositionSnapshot).all()],
                protections=[self._row(row) for row in session.query(models.ProtectionRecord).all()],
                exchange=self._exchange_view(symbols),
                v2_exchange_order_ids={
                    str(row.exchange_order_id)
                    for row in session.query(v2_models.V2ExchangeOrder).all()
                    if row.exchange_order_id
                },
            )

    def confirm_exchange_order(self, *, symbol: str, exchange_order_id: str) -> dict[str, Any] | None:
        from services.execution.gateway import _normalize_binance_symbol

        client = self._get_gateway().client
        try:
            order = client.fetch_order(exchange_order_id, _normalize_binance_symbol(symbol))
            trades = client.fetch_my_trades(
                _normalize_binance_symbol(symbol), since=None, limit=1000, params={"orderId": exchange_order_id}
            )
        except Exception:  # Network failures are evidence failures, never retried as a write.
            return None
        trade_ids = [str(item.get("id")) for item in trades or [] if item.get("id") is not None]
        return {
            "exists": bool(order),
            "status": str((order or {}).get("status") or "").lower(),
            "trade_ids": trade_ids,
            "filled_quantity": str((order or {}).get("filled") or "0"),
            "average_fill_price": str((order or {}).get("average") or "0"),
            "quantity": str((order or {}).get("amount") or (order or {}).get("info", {}).get("origQty") or "0"),
            "side": str((order or {}).get("side") or "").lower(),
            "reduce_only": bool((order or {}).get("reduceOnly") or (order or {}).get("info", {}).get("reduceOnly")),
        }

    def _exchange_view(self, symbols: tuple[str, ...]) -> ExchangeView:
        from services.execution.gateway import _normalize_binance_symbol

        gateway = self._get_gateway()
        client = gateway.client
        try:
            raw_positions = client.fetch_positions()
            if not isinstance(raw_positions, list) or not raw_positions:
                return ExchangeView(complete=False, error="Binance returned no position rows")
            positions: dict[str, str] = dict.fromkeys(symbols, "0")
            for position in raw_positions:
                symbol = self._platform_symbol(position.get("symbol"))
                if symbol in positions:
                    positions[symbol] = str(
                        position.get("contracts") or position.get("info", {}).get("positionAmt") or "0"
                    )
            open_order_ids: dict[str, set[str]] = {symbol: set() for symbol in symbols}
            for symbol in symbols:
                for order in client.fetch_open_orders(_normalize_binance_symbol(symbol)):
                    order_id = order.get("id") or order.get("orderId") or order.get("algoId")
                    if order_id is not None:
                        open_order_ids[symbol].add(str(order_id))
            market_ids = {symbol.replace("/", "") for symbol in symbols}
            for order in gateway._fetch_open_algo_orders(market_ids=market_ids):
                symbol = self._platform_symbol(order.get("symbol"))
                order_id = order.get("algoId") or order.get("orderId") or order.get("id")
                if symbol in open_order_ids and order_id is not None:
                    open_order_ids[symbol].add(str(order_id))
            return ExchangeView(positions=positions, open_order_ids=open_order_ids)
        except Exception as exc:  # Read failures are unknown, never flat.
            return ExchangeView(complete=False, error=f"exchange read failed: {type(exc).__name__}: {exc}")

    def _get_gateway(self):
        if self._gateway is None:
            from services.execution.gateway import BinanceUsdtPerpetualGateway

            self._gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
        return self._gateway

    def _scheduler_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.scheduler_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _platform_symbol(value: Any) -> str:
        raw = str(value or "").replace(":USDT", "")
        if raw.endswith("USDT") and "/" not in raw:
            return f"{raw.removesuffix('USDT')}/USDT"
        return raw

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}

    @staticmethod
    def _model(value: Any) -> dict[str, Any]:
        return value.model_dump(mode="json") if value is not None else {}


def _session_path(output_dir: Path, session_id: str) -> Path:
    return output_dir / f"legacy-natural-proof-session-{session_id}.json"


def write_session(evidence: LegacyNaturalEvidence, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _session_path(output_dir, evidence.proof_session_id)
    path.write_text(json.dumps(asdict(evidence), indent=2, default=str), encoding="utf-8")
    return path


def write_evidence(evidence: LegacyNaturalEvidence) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = EVIDENCE_DIR / f"legacy_natural_cycle_{stamp}.json"
    path.write_text(json.dumps(asdict(evidence), indent=2, default=str), encoding="utf-8")
    return path


def load_session(path: Path) -> LegacyNaturalEvidence:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return LegacyNaturalEvidence(**payload)


def observe(
    observer: LegacyNaturalObserver,
    *,
    output_dir: Path,
    entry_timeout: timedelta,
    lifecycle_timeout: timedelta,
    poll_seconds: int,
    resume: bool = False,
) -> LegacyNaturalEvidence:
    evidence = observer.resume() if resume else observer.start()
    write_session(evidence, output_dir)
    if evidence.status.startswith(("BLOCKED", "FAILED", "PASSED")):
        return evidence
    started = _parse_time(evidence.started_at) or _utcnow()
    while True:
        evidence = observer.poll()
        write_session(evidence, output_dir)
        if evidence.status.startswith(("BLOCKED", "FAILED", "PASSED")):
            return evidence
        now = _utcnow()
        if evidence.entry_exchange_order_id is None and now >= started + entry_timeout:
            return observer.finish_timeout()
        if now >= started + lifecycle_timeout:
            return observer.finish_timeout()
        time.sleep(max(poll_seconds, 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only natural legacy Scheduler Testnet proof")
    parser.add_argument("--database-url", default=DEFAULT_DB_URL)
    parser.add_argument("--scheduler-state", type=Path, default=DEFAULT_SCHEDULER_STATE)
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT"])
    parser.add_argument("--entry-timeout-hours", type=float, default=72)
    parser.add_argument("--lifecycle-timeout-hours", type=float, default=168)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()

    source = LiveLegacySource(database_url=args.database_url, scheduler_state_path=args.scheduler_state)
    prior = load_session(args.resume) if args.resume else None
    observer = LegacyNaturalObserver(source, symbols=tuple(args.symbols), evidence=prior)
    evidence = observe(
        observer,
        output_dir=args.output_dir,
        entry_timeout=timedelta(hours=args.entry_timeout_hours),
        lifecycle_timeout=timedelta(hours=args.lifecycle_timeout_hours),
        poll_seconds=args.poll_seconds,
        resume=prior is not None,
    )
    session_path = write_session(evidence, args.output_dir)
    print(f"status={evidence.status}")
    print(f"session={session_path}")
    if evidence.passed:
        print(f"evidence={write_evidence(evidence)}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
