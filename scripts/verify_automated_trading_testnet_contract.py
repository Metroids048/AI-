#!/usr/bin/env python3
"""Task 16: real Binance Testnet contract verification (plan section 16.4).

Proves the *infrastructure* contract with real network calls: credentials,
server time, market rules, a minimal market entry, real order id, real trade
id, fill receipt, stop/take-profit submission, reduce-only exit, and a final
zeroed position.

This is explicitly NOT proof that the natural strategy loop works. The emitted
evidence is tagged:

    proof_type      = TESTNET_CONTRACT
    natural_strategy = false

Task 17 is the only script permitted to claim the natural loop.

Safety:
- Requires V2_TESTNET_CONTRACT_ENABLED=true and real Testnet credentials.
- Refuses to run if mainnet is configured in any way.
- Uses the smallest viable notional.
- Always attempts compensating cleanup on failure (cancel orders, flatten).
- Never writes credentials into the evidence bundle.

Usage:
    $env:V2_TESTNET_CONTRACT_ENABLED="true"
    python scripts/verify_automated_trading_testnet_contract.py --symbol BTC/USDT
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


@dataclass
class ContractStep:
    """One verification step and its real outcome."""

    name: str
    passed: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContractEvidence:
    """Evidence bundle. Contains no secrets — only hashes and ids."""

    proof_type: str = "TESTNET_CONTRACT"
    natural_strategy: bool = False
    symbol: str = ""
    started_at: str = ""
    completed_at: str = ""
    account_id_hash: str | None = None
    exchange_server_time: str | None = None
    entry_exchange_order_id: str | None = None
    entry_trade_ids: list[str] = field(default_factory=list)
    entry_avg_fill_price: str | None = None
    entry_filled_quantity: str | None = None
    stop_exchange_order_id: str | None = None
    take_profit_exchange_order_id: str | None = None
    exit_exchange_order_id: str | None = None
    exit_trade_ids: list[str] = field(default_factory=list)
    final_exchange_position_qty: str | None = None
    network_calls: int = 0
    real_exchange_orders: int = 0
    steps: list[ContractStep] = field(default_factory=list)
    overall_passed: bool = False

    def add(self, step: ContractStep) -> None:
        self.steps.append(step)
        marker = "PASS" if step.passed else "FAIL"
        print(f"  [{marker}] {step.name}: {step.detail}")


def _preflight() -> tuple[bool, str]:
    """Refuse to run unless explicitly authorised and testnet-only."""
    if os.getenv("V2_TESTNET_CONTRACT_ENABLED", "false").lower() != "true":
        return False, "V2_TESTNET_CONTRACT_ENABLED is not true; refusing to place real orders"

    from shared.config import settings

    if not settings.binance_use_testnet:
        return False, "BINANCE_USE_TESTNET is false; mainnet is never permitted here"
    if settings.live_trading_enabled:
        return False, "LIVE_TRADING_ENABLED is true; mainnet is never permitted here"
    if not (settings.binance_api_key and settings.binance_api_secret):
        return False, "Binance Testnet credentials are missing"

    return True, "preflight ok"


def _hash_account(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()[:16]


def run_contract(symbol: str, notional_usdt: Decimal) -> ContractEvidence:
    """Execute the full contract sequence against real Binance Testnet."""
    evidence = ContractEvidence(symbol=symbol, started_at=datetime.now(UTC).isoformat())

    ok, detail = _preflight()
    evidence.add(ContractStep("preflight", ok, detail))
    if not ok:
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    from services.automated_trading.domain.client_order_id import (
        entry_client_order_id,
        exit_client_order_id,
        stop_client_order_id,
        target_client_order_id,
    )
    from services.automated_trading.domain.commands import (
        SubmitEntryToExchange,
        SubmitProtectionOrders,
        SubmitReduceOnlyExit,
    )
    from services.automated_trading.infrastructure.binance_adapter import (
        BinanceTestnetAdapter,
    )

    adapter = BinanceTestnetAdapter()
    intent_id = f"contract-{int(time.time())}"

    # --- 2. Authoritative snapshot (server time + account identity) ---
    try:
        snapshot = adapter.fetch_authoritative_snapshot()
        evidence.network_calls += 1
        evidence.exchange_server_time = snapshot.snapshot_timestamp.isoformat()
        evidence.account_id_hash = _hash_account(str(snapshot.balance))
        evidence.add(
            ContractStep(
                "authoritative_snapshot",
                True,
                f"server_time={evidence.exchange_server_time} equity={snapshot.equity}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        evidence.add(ContractStep("authoritative_snapshot", False, f"{type(exc).__name__}: {exc}"))
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    # --- 3. Market rules ---
    try:
        market = adapter.fetch_market_snapshot(symbol)
        evidence.network_calls += 1
        evidence.add(
            ContractStep(
                "market_rules",
                True,
                f"price={market.current_price} tick={market.tick_size} step={market.step_size} "
                f"min_notional={market.min_notional}",
                {
                    "tick_size": str(market.tick_size),
                    "step_size": str(market.step_size),
                    "min_notional": str(market.min_notional),
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        evidence.add(ContractStep("market_rules", False, f"{type(exc).__name__}: {exc}"))
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    # --- 4. Minimal market entry ---
    from services.automated_trading.application.entry_service import round_quantity_to_step

    target_notional = max(notional_usdt, market.min_notional)
    raw_qty = target_notional / market.current_price
    quantity = round_quantity_to_step(raw_qty, market.step_size)
    if quantity <= 0:
        evidence.add(ContractStep("entry_submit", False, "computed quantity rounds to zero"))
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    entry_coid = entry_client_order_id(intent_id)
    try:
        receipt = adapter.submit_market_order(
            SubmitEntryToExchange(
                intent_id=intent_id,
                quantity=quantity,
                leverage=1,
                client_order_id=entry_coid,
            ),
            symbol,
            "buy",
        )
        evidence.network_calls += 1
        evidence.real_exchange_orders += 1
        evidence.entry_exchange_order_id = receipt.exchange_order_id
        evidence.add(
            ContractStep("entry_submit", True, f"exchange_order_id={receipt.exchange_order_id} qty={quantity}")
        )
    except Exception as exc:  # noqa: BLE001
        evidence.add(ContractStep("entry_submit", False, f"{type(exc).__name__}: {exc}"))
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    # --- 5. Real fill receipt ---
    avg_price: Decimal | None = None
    filled_qty = Decimal("0")
    try:
        fills = adapter.fetch_fills(symbol, receipt.exchange_order_id)
        evidence.network_calls += 1
        trade_ids = [f.trade_id for f in fills]
        filled_qty = sum((f.filled_quantity for f in fills), Decimal("0"))
        notional = sum((f.filled_quantity * f.fill_price for f in fills), Decimal("0"))
        avg_price = (notional / filled_qty) if filled_qty > 0 else None

        evidence.entry_trade_ids = trade_ids
        evidence.entry_filled_quantity = str(filled_qty)
        evidence.entry_avg_fill_price = str(avg_price) if avg_price else None

        good = bool(trade_ids) and filled_qty > 0 and avg_price is not None and avg_price > 0
        evidence.add(
            ContractStep(
                "entry_fill_receipt",
                good,
                f"trade_ids={trade_ids} filled={filled_qty} avg_price={avg_price}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        evidence.add(ContractStep("entry_fill_receipt", False, f"{type(exc).__name__}: {exc}"))

    # --- 6. Protection orders (priced from the REAL fill) ---
    if avg_price and avg_price > 0 and filled_qty > 0:
        stop_price = (avg_price * Decimal("0.99")).quantize(market.tick_size)
        target_price = (avg_price * Decimal("1.02")).quantize(market.tick_size)
        try:
            stop_receipt, target_receipt = adapter.submit_protection(
                SubmitProtectionOrders(
                    position_id=intent_id,
                    stop_loss_price=stop_price,
                    take_profit_price=target_price,
                    quantity=filled_qty,
                    stop_client_order_id=stop_client_order_id(intent_id),
                    take_profit_client_order_id=target_client_order_id(intent_id),
                ),
                symbol,
                "sell",
            )
            evidence.network_calls += 1
            evidence.real_exchange_orders += 1
            evidence.stop_exchange_order_id = stop_receipt.exchange_order_id if stop_receipt else None
            evidence.take_profit_exchange_order_id = target_receipt.exchange_order_id if target_receipt else None
            evidence.add(
                ContractStep(
                    "protection_submit",
                    bool(evidence.stop_exchange_order_id),
                    f"stop={evidence.stop_exchange_order_id} target={evidence.take_profit_exchange_order_id} "
                    f"(stop_price={stop_price} from real fill {avg_price})",
                )
            )
        except Exception as exc:  # noqa: BLE001
            evidence.add(ContractStep("protection_submit", False, f"{type(exc).__name__}: {exc}"))

    # --- 7. Reduce-only exit (always attempted: this is also the cleanup) ---
    if filled_qty > 0:
        try:
            exit_receipt = adapter.submit_reduce_only_exit(
                SubmitReduceOnlyExit(
                    position_id=intent_id,
                    quantity=filled_qty,
                    client_order_id=exit_client_order_id(intent_id),
                ),
                symbol,
                "sell",
            )
            evidence.network_calls += 1
            evidence.real_exchange_orders += 1
            evidence.exit_exchange_order_id = exit_receipt.exchange_order_id
            evidence.add(ContractStep("reduce_only_exit", True, f"exchange_order_id={exit_receipt.exchange_order_id}"))

            exit_fills = adapter.fetch_fills(symbol, exit_receipt.exchange_order_id)
            evidence.network_calls += 1
            evidence.exit_trade_ids = [f.trade_id for f in exit_fills]
            evidence.add(
                ContractStep(
                    "exit_fill_receipt",
                    bool(evidence.exit_trade_ids),
                    f"trade_ids={evidence.exit_trade_ids}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            evidence.add(ContractStep("reduce_only_exit", False, f"{type(exc).__name__}: {exc}"))

    # --- 8. Cancel any residual protection ---
    for label, oid in (
        ("stop", evidence.stop_exchange_order_id),
        ("take_profit", evidence.take_profit_exchange_order_id),
    ):
        if not oid:
            continue
        try:
            adapter.cancel_order(symbol, oid)
            evidence.network_calls += 1
            evidence.add(ContractStep(f"cancel_{label}", True, f"cancelled {oid}"))
        except Exception as exc:  # noqa: BLE001
            evidence.add(ContractStep(f"cancel_{label}", False, f"{type(exc).__name__}: {exc}"))

    # --- 9. Final position must be zero ---
    try:
        final_snapshot = adapter.fetch_authoritative_snapshot()
        evidence.network_calls += 1
        final_pos = next((p for p in final_snapshot.positions if p.symbol == symbol), None)
        final_qty = Decimal(str(final_pos.quantity)) if final_pos else Decimal("0")
        evidence.final_exchange_position_qty = str(final_qty)
        evidence.add(ContractStep("final_position_zero", final_qty == 0, f"final exchange position = {final_qty}"))
    except Exception as exc:  # noqa: BLE001
        evidence.add(ContractStep("final_position_zero", False, f"{type(exc).__name__}: {exc}"))

    evidence.completed_at = datetime.now(UTC).isoformat()
    evidence.overall_passed = all(step.passed for step in evidence.steps)
    return evidence


def write_evidence(evidence: ContractEvidence) -> Path:
    """Persist the evidence bundle (never contains credentials)."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = EVIDENCE_DIR / f"testnet_contract_{stamp}.json"
    path.write_text(json.dumps(asdict(evidence), indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Real Binance Testnet contract verification (Task 16)")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--notional", default="20", help="Target notional in USDT (min_notional wins if larger)")
    args = parser.parse_args()

    print("=" * 72)
    print("Task 16: Binance Testnet CONTRACT verification")
    print("proof_type=TESTNET_CONTRACT  natural_strategy=false")
    print("=" * 72)

    evidence = run_contract(args.symbol, Decimal(args.notional))
    path = write_evidence(evidence)

    print("-" * 72)
    print(f"network_calls        : {evidence.network_calls}")
    print(f"real_exchange_orders : {evidence.real_exchange_orders}")
    print(f"entry_order_id       : {evidence.entry_exchange_order_id}")
    print(f"entry_trade_ids      : {evidence.entry_trade_ids}")
    print(f"exit_order_id        : {evidence.exit_exchange_order_id}")
    print(f"final_position_qty   : {evidence.final_exchange_position_qty}")
    print(f"evidence             : {path}")
    print("-" * 72)

    if evidence.overall_passed:
        print("RESULT: TESTNET CONTRACT PASSED")
        print("NOTE: this does NOT prove the natural strategy loop. Run Task 17 for that.")
        return 0

    failed = [s.name for s in evidence.steps if not s.passed]
    print(f"RESULT: FAILED steps: {failed}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
