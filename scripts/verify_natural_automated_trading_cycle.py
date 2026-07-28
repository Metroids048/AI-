#!/usr/bin/env python3
"""Task 17: natural Scheduler E2E verification (plan section 16.5 / Gate 17).

This is the ONLY script permitted to emit:

    proof_type = NATURAL_SCHEDULER_TESTNET

It observes the ordinary RuntimeScheduler completing a full lifecycle without
any shortcut. Per plan section 17, the following are all forbidden and the
script actively refuses to count them as evidence:

- the Acceptance service or any fixed round-trip helper
- manual position opening
- synthetic local fills
- calling entry/exit services directly, bypassing the Scheduler
- forcing a database state change to trigger a close

Instead it *watches*: it polls V2 state and waits for a natural entry, real
protection, a natural exit trigger, and a final healthy reconciliation.

Evidence required by Gate 17 (all must be real and present):
    cycle_id, decision_id, candidate_id, intent_id,
    entry exchange_order_id, entry trade_ids, position_group_id,
    stop/tp exchange_order_ids, exit trigger, exit exchange_order_id,
    exit trade_ids, final exchange position = 0,
    final local position = CLOSED, reconciliation = HEALTHY

Usage:
    $env:V2_NATURAL_E2E_ENABLED="true"
    python scripts/verify_natural_automated_trading_cycle.py --timeout-minutes 180
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

EVIDENCE_DIR = Path("docs/evidence/automated_trading_v2")

# Any of these appearing in the observed lane/strategy marks the evidence invalid.
FORBIDDEN_MARKERS = (
    "acceptance",
    "manual",
    "synthetic",
    "canary",
    "emulator",
    "fake",
)


@dataclass
class NaturalCycleEvidence:
    """Gate 17 evidence bundle. Every id here must come from real observation."""

    proof_type: str = "NATURAL_SCHEDULER_TESTNET"
    natural_strategy: bool = True
    started_at: str = ""
    completed_at: str = ""
    symbol: str | None = None

    # Decision chain
    cycle_id: str | None = None
    decision_id: str | None = None
    candidate_id: str | None = None
    intent_id: str | None = None
    strategy_id: str | None = None
    lane: str | None = None

    # Entry
    entry_exchange_order_id: str | None = None
    entry_trade_ids: list[str] = field(default_factory=list)
    entry_avg_fill_price: str | None = None
    position_group_id: str | None = None

    # Protection
    stop_exchange_order_id: str | None = None
    take_profit_exchange_order_id: str | None = None

    # Exit
    exit_trigger: str | None = None
    exit_exchange_order_id: str | None = None
    exit_trade_ids: list[str] = field(default_factory=list)

    # Final state
    final_exchange_position_qty: str | None = None
    final_local_position_state: str | None = None
    final_reconciliation_status: str | None = None

    # Integrity
    used_acceptance_shortcut: bool = False
    used_manual_intervention: bool = False
    used_synthetic_fill: bool = False
    observed_cycles: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        stamped = f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {message}"
        self.notes.append(stamped)
        print(f"  {stamped}")

    @property
    def gate17_passed(self) -> bool:
        """Every Gate 17 condition, evaluated strictly."""
        if self.used_acceptance_shortcut or self.used_manual_intervention or self.used_synthetic_fill:
            return False
        if self.final_exchange_position_qty is None:
            return False
        try:
            if Decimal(self.final_exchange_position_qty) != 0:
                return False
        except Exception:  # noqa: BLE001
            return False

        return all(
            [
                bool(self.cycle_id),
                bool(self.decision_id),
                bool(self.candidate_id),
                bool(self.intent_id),
                bool(self.entry_exchange_order_id),
                bool(self.entry_trade_ids),
                bool(self.position_group_id),
                bool(self.stop_exchange_order_id),
                bool(self.exit_trigger),
                bool(self.exit_exchange_order_id),
                bool(self.exit_trade_ids),
                self.final_local_position_state == "CLOSED",
                self.final_reconciliation_status == "HEALTHY",
            ]
        )

    def missing_requirements(self) -> list[str]:
        """Human-readable list of what is still absent."""
        checks = {
            "cycle_id": bool(self.cycle_id),
            "decision_id": bool(self.decision_id),
            "candidate_id": bool(self.candidate_id),
            "intent_id": bool(self.intent_id),
            "entry_exchange_order_id": bool(self.entry_exchange_order_id),
            "entry_trade_ids": bool(self.entry_trade_ids),
            "position_group_id": bool(self.position_group_id),
            "stop_exchange_order_id": bool(self.stop_exchange_order_id),
            "exit_trigger": bool(self.exit_trigger),
            "exit_exchange_order_id": bool(self.exit_exchange_order_id),
            "exit_trade_ids": bool(self.exit_trade_ids),
            "final_local_position_state==CLOSED": self.final_local_position_state == "CLOSED",
            "final_reconciliation==HEALTHY": self.final_reconciliation_status == "HEALTHY",
            "final_exchange_position==0": (
                self.final_exchange_position_qty is not None and Decimal(self.final_exchange_position_qty) == 0
            ),
            "no_acceptance_shortcut": not self.used_acceptance_shortcut,
            "no_manual_intervention": not self.used_manual_intervention,
            "no_synthetic_fill": not self.used_synthetic_fill,
        }
        return [name for name, ok in checks.items() if not ok]


def _preflight() -> tuple[bool, str]:
    """Refuse unless explicitly authorised, testnet-only, and V2 is the writer."""
    if os.getenv("V2_NATURAL_E2E_ENABLED", "false").lower() != "true":
        return False, "V2_NATURAL_E2E_ENABLED is not true; refusing to run a real-money-path observation"

    from shared.config import settings

    if not settings.binance_use_testnet:
        return False, "BINANCE_USE_TESTNET is false; mainnet is never permitted"
    if settings.live_trading_enabled:
        return False, "LIVE_TRADING_ENABLED is true; mainnet is never permitted"

    engine = getattr(settings, "automated_trading_engine", "legacy").lower()
    if engine != "v2_active":
        return False, f"AUTOMATED_TRADING_ENGINE={engine}; Task 17 requires v2_active (V2 as sole writer)"

    return True, "preflight ok"


def _flag_forbidden_source(evidence: NaturalCycleEvidence, *values: str | None) -> None:
    """Mark the evidence invalid if any observed source looks like a shortcut."""
    for value in values:
        if not value:
            continue
        lowered = value.lower()
        for marker in FORBIDDEN_MARKERS:
            if marker in lowered:
                if marker in ("acceptance", "canary"):
                    evidence.used_acceptance_shortcut = True
                elif marker == "manual":
                    evidence.used_manual_intervention = True
                else:
                    evidence.used_synthetic_fill = True
                evidence.note(f"INVALID: observed forbidden source marker '{marker}' in '{value}'")


def _poll_v2_state() -> dict[str, Any]:
    """Read current V2 runtime state from the repository (observation only)."""
    from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
    from services.database import get_session_factory

    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        return {
            "positions": repo.get_open_positions(None) if hasattr(repo, "get_open_positions") else [],
            "repo": None,  # never hold a session outside the block
        }


def observe_natural_cycle(symbol_filter: str | None, timeout_minutes: int, poll_seconds: int) -> NaturalCycleEvidence:
    """Watch the ordinary Scheduler until a full natural lifecycle completes."""
    evidence = NaturalCycleEvidence(started_at=datetime.now(UTC).isoformat(), symbol=symbol_filter)

    ok, detail = _preflight()
    evidence.note(f"preflight: {detail}")
    if not ok:
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    deadline = time.time() + timeout_minutes * 60
    evidence.note(f"observing for up to {timeout_minutes} minutes; polling every {poll_seconds}s")
    evidence.note("NOTE: this script only observes. It never opens, closes, or mutates anything.")

    saw_entry = False
    saw_exit = False

    while time.time() < deadline:
        evidence.observed_cycles += 1

        try:
            state = _poll_v2_state()
        except Exception as exc:  # noqa: BLE001
            evidence.note(f"state read failed: {type(exc).__name__}: {exc}")
            time.sleep(poll_seconds)
            continue

        positions = state.get("positions") or []
        for pos in positions:
            pos_symbol = getattr(pos, "symbol", None)
            if symbol_filter and pos_symbol != symbol_filter:
                continue

            strategy_id = getattr(pos, "strategy_id", None)
            lane = getattr(pos, "lane", None)
            _flag_forbidden_source(evidence, strategy_id, lane)

            state_value = getattr(pos, "state", None)

            if not saw_entry and state_value in ("POSITION_PROJECTED", "PROTECTED"):
                saw_entry = True
                evidence.symbol = pos_symbol
                evidence.strategy_id = strategy_id
                evidence.lane = lane
                evidence.position_group_id = getattr(pos, "position_id", None)
                evidence.cycle_id = getattr(pos, "cycle_id", None)
                evidence.intent_id = getattr(pos, "entry_intent_id", None)
                evidence.entry_exchange_order_id = getattr(pos, "exchange_entry_order_id", None)
                avg = getattr(pos, "average_entry_price", None)
                evidence.entry_avg_fill_price = str(avg) if avg is not None else None
                evidence.note(
                    f"natural ENTRY observed: {pos_symbol} state={state_value} "
                    f"order_id={evidence.entry_exchange_order_id}"
                )

            if saw_entry and state_value == "PROTECTED" and not evidence.stop_exchange_order_id:
                evidence.stop_exchange_order_id = getattr(pos, "stop_exchange_order_id", None)
                evidence.take_profit_exchange_order_id = getattr(pos, "take_profit_exchange_order_id", None)
                evidence.note(f"real PROTECTION observed: stop={evidence.stop_exchange_order_id}")

            if saw_entry and state_value == "CLOSED" and not saw_exit:
                saw_exit = True
                evidence.final_local_position_state = "CLOSED"
                evidence.exit_trigger = getattr(pos, "exit_reason", None)
                evidence.exit_exchange_order_id = getattr(pos, "exchange_exit_order_id", None)
                evidence.note(
                    f"natural EXIT observed: trigger={evidence.exit_trigger} order_id={evidence.exit_exchange_order_id}"
                )

        if saw_entry and saw_exit:
            evidence.note("full natural lifecycle observed; verifying final exchange truth")
            break

        time.sleep(poll_seconds)

    if not (saw_entry and saw_exit):
        evidence.note("timeout: a complete natural lifecycle was NOT observed")
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    # Final exchange truth must confirm flat.
    try:
        from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

        adapter = BinanceTestnetAdapter()
        snapshot = adapter.fetch_authoritative_snapshot()
        pos = next((p for p in snapshot.positions if p.symbol == evidence.symbol), None)
        qty = Decimal(str(pos.quantity)) if pos else Decimal("0")
        evidence.final_exchange_position_qty = str(qty)
        evidence.note(f"final exchange position: {qty}")
    except Exception as exc:  # noqa: BLE001
        evidence.note(f"final exchange read failed: {type(exc).__name__}: {exc}")

    # Final reconciliation must be HEALTHY.
    try:
        from services.automated_trading.application.reconciliation_service import (
            LocalStateView,
            reconcile,
        )
        from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

        adapter = BinanceTestnetAdapter()
        snapshot = adapter.fetch_authoritative_snapshot()
        result = reconcile(snapshot, LocalStateView())
        evidence.final_reconciliation_status = result.status.value
        evidence.note(f"final reconciliation: {result.status.value}")
    except Exception as exc:  # noqa: BLE001
        evidence.note(f"final reconciliation failed: {type(exc).__name__}: {exc}")

    evidence.completed_at = datetime.now(UTC).isoformat()
    return evidence


def write_evidence(evidence: NaturalCycleEvidence) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = EVIDENCE_DIR / f"natural_cycle_{stamp}.json"
    path.write_text(json.dumps(asdict(evidence), indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 17: natural Scheduler E2E observation")
    parser.add_argument("--symbol", default=None, help="Optional symbol filter, e.g. BTC/USDT")
    parser.add_argument("--timeout-minutes", type=int, default=180)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()

    print("=" * 72)
    print("Task 17: NATURAL Scheduler E2E observation")
    print("proof_type=NATURAL_SCHEDULER_TESTNET")
    print("This script observes only. No manual open/close. No acceptance shortcut.")
    print("=" * 72)

    evidence = observe_natural_cycle(args.symbol, args.timeout_minutes, args.poll_seconds)
    path = write_evidence(evidence)

    print("-" * 72)
    print(f"evidence: {path}")

    if evidence.gate17_passed:
        print("\nRESULT: GATE 17 PASSED")
        print("Binance Testnet 自然自动开平单链路已打通。")
        return 0

    print("\nRESULT: GATE 17 NOT PASSED")
    print("missing / invalid:")
    for item in evidence.missing_requirements():
        print(f"  - {item}")
    print("\nGate 17 has not passed; the natural loop claim is NOT permitted.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
