#!/usr/bin/env python3
"""Task 16: real Binance Testnet contract verification (plan section 16.4).

Proves the *infrastructure* contract with real network calls: credentials,
server time, market rules, a minimal market entry, real order id, real trade
id, fill receipt, stop/take-profit submission, reduce-only exit, and a final
position restored to the pre-run exchange baseline.

This is explicitly NOT proof that the natural strategy loop works. The emitted
evidence is tagged:

    proof_type      = TESTNET_CONTRACT
    natural_strategy = false

Task 17 is the only script permitted to claim the natural loop.

Safety:
- Requires V2_TESTNET_CONTRACT_ENABLED=true and real Testnet credentials.
- Refuses to run if mainnet is configured in any way.
- Uses the smallest viable notional.
- Preserves pre-existing manual position quantity and open orders as baseline.
- Always attempts compensating cleanup on failure (cancel contract orders, restore baseline).
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
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

EVIDENCE_DIR = Path("docs/evidence/automated_trading_v2")
FILL_TIMEOUT_SECONDS = 30.0
FILL_POLL_SECONDS = 0.5


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
    baseline_exchange_position_direction: str | None = None
    baseline_exchange_position_qty: str | None = None
    baseline_exchange_open_orders: int | None = None
    contract_direction: str | None = None
    entry_exchange_order_id: str | None = None
    entry_trade_ids: list[str] = field(default_factory=list)
    entry_avg_fill_price: str | None = None
    entry_filled_quantity: str | None = None
    stop_exchange_order_id: str | None = None
    take_profit_exchange_order_id: str | None = None
    exit_exchange_order_id: str | None = None
    exit_trade_ids: list[str] = field(default_factory=list)
    final_exchange_position_qty: str | None = None
    final_exchange_open_orders: int | None = None
    final_new_open_orders: int | None = None
    network_calls: int = 0
    real_exchange_orders: int = 0
    steps: list[ContractStep] = field(default_factory=list)
    overall_passed: bool = False
    execution_contract_health: bool = False
    natural_strategy_business_recovery: bool = False
    natural_auto_trading_recovery: bool = False

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


def _poll_fills(
    adapter: Any,
    *,
    symbol: str,
    exchange_order_id: str,
    expected_quantity: Decimal,
    timeout_seconds: float = FILL_TIMEOUT_SECONDS,
    poll_seconds: float = FILL_POLL_SECONDS,
) -> list[Any]:
    """Poll an exchange order until at least one real fill is visible."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        fills = list(adapter.fetch_fills(symbol, exchange_order_id))
        filled_quantity = sum((fill.filled_quantity for fill in fills), Decimal("0"))
        if filled_quantity >= expected_quantity:
            return fills
        if time.monotonic() >= deadline:
            raise TimeoutError(f"no fills for exchange order {exchange_order_id} within {timeout_seconds}s")
        time.sleep(poll_seconds)


def _symbol_state(snapshot: Any, symbol: str) -> tuple[Any | None, list[Any]]:
    position = next((item for item in snapshot.positions if item.symbol == symbol), None)
    orders = [item for item in snapshot.pending_orders if item.symbol == symbol]
    return position, orders


def _contract_excess_quantity(
    position: Any | None,
    *,
    baseline_direction: str | None,
    baseline_quantity: Decimal,
    contract_direction: str,
) -> Decimal:
    """Return only the contract quantity added above the account baseline.

    On Binance one-way mode, manual and automated lots share one aggregate
    exchange position. Gate 16 therefore adds risk in the baseline direction
    and cleanup restores the exact pre-run quantity instead of flattening.
    """
    if position is None:
        return Decimal("0")
    direction = str(position.direction)
    quantity = Decimal(str(position.quantity))
    if direction != contract_direction:
        return Decimal("0")
    if baseline_direction is not None and direction != baseline_direction:
        return Decimal("0")
    return max(Decimal("0"), quantity - baseline_quantity)


def _position_matches_baseline(
    position: Any | None,
    *,
    baseline_direction: str | None,
    baseline_quantity: Decimal,
) -> bool:
    if position is None:
        return baseline_direction is None and baseline_quantity == 0
    return str(position.direction) == baseline_direction and Decimal(str(position.quantity)) == baseline_quantity


def _round_quantity_up_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    """Return the smallest valid step multiple that is not below quantity."""

    if step <= 0:
        raise ValueError("quantity step must be positive")
    units = (quantity / step).to_integral_value(rounding=ROUND_CEILING)
    return units * step


def run_contract(symbol: str, notional_usdt: Decimal) -> ContractEvidence:
    """Execute the full contract sequence against real Binance Testnet."""
    evidence = ContractEvidence(symbol=symbol, started_at=datetime.now(UTC).isoformat())

    if symbol != "BTC/USDT" or notional_usdt != Decimal("20"):
        evidence.add(
            ContractStep(
                "authorized_scope",
                False,
                "Gate 16 is locked to BTC/USDT with target notional 20 USDT",
            )
        )
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

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
    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure.binance_adapter import (
        BinanceTestnetAdapter,
    )

    adapter = BinanceTestnetAdapter(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    intent_id = f"contract-{int(time.time())}"

    baseline_direction: str | None = None
    baseline_quantity = Decimal("0")
    baseline_order_ids: set[str] = set()
    contract_direction = "long"
    entry_side = "buy"
    exit_side = "sell"

    # --- 2. Authoritative snapshot (server time + account identity) ---
    try:
        snapshot = adapter.fetch_authoritative_snapshot()
        evidence.network_calls += 1
        evidence.exchange_server_time = snapshot.snapshot_timestamp.isoformat()
        evidence.add(
            ContractStep(
                "authoritative_snapshot",
                True,
                f"server_time={evidence.exchange_server_time}",
            )
        )
        existing_position, existing_orders = _symbol_state(snapshot, symbol)
        if existing_position is not None:
            baseline_direction = str(existing_position.direction)
            baseline_quantity = Decimal(str(existing_position.quantity))
        contract_direction = baseline_direction or "long"
        entry_side = "buy" if contract_direction == "long" else "sell"
        exit_side = "sell" if contract_direction == "long" else "buy"
        baseline_order_ids = {str(item.exchange_order_id) for item in existing_orders}
        evidence.baseline_exchange_position_direction = baseline_direction
        evidence.baseline_exchange_position_qty = str(baseline_quantity)
        evidence.baseline_exchange_open_orders = len(existing_orders)
        evidence.contract_direction = contract_direction
        compatible_baseline = contract_direction in {"long", "short"}
        evidence.add(
            ContractStep(
                "preflight_exchange_baseline",
                compatible_baseline,
                f"direction={baseline_direction} position={baseline_quantity} contract={contract_direction} "
                f"open_orders={len(existing_orders)}; contract restores this baseline",
            )
        )
        if not compatible_baseline:
            evidence.completed_at = datetime.now(UTC).isoformat()
            return evidence
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

    # --- 4. Minimal market entry and fail-safe lifecycle ---
    target_notional = max(notional_usdt, market.min_notional)
    raw_qty = target_notional / market.current_price
    quantity = _round_quantity_up_to_step(raw_qty, market.step_size)
    if quantity <= 0:
        evidence.add(ContractStep("entry_submit", False, "computed quantity rounds to zero"))
        evidence.completed_at = datetime.now(UTC).isoformat()
        return evidence

    entry_coid = entry_client_order_id(intent_id)
    receipt: Any | None = None
    avg_price: Decimal | None = None
    filled_qty = Decimal("0")
    try:
        try:
            receipt = adapter.submit_market_order(
                SubmitEntryToExchange(
                    intent_id=intent_id,
                    quantity=quantity,
                    leverage=1,
                    client_order_id=entry_coid,
                ),
                symbol,
                entry_side,
            )
            evidence.network_calls += 1
            evidence.real_exchange_orders += 1
            evidence.entry_exchange_order_id = receipt.exchange_order_id
            evidence.add(
                ContractStep("entry_submit", True, f"exchange_order_id={receipt.exchange_order_id} qty={quantity}")
            )
        except Exception as exc:  # noqa: BLE001
            evidence.add(ContractStep("entry_submit", False, f"{type(exc).__name__}: {exc}"))
            try:
                receipt = adapter.query_order_by_client_id(symbol, entry_coid)
                evidence.network_calls += 1
                if receipt is not None:
                    evidence.entry_exchange_order_id = receipt.exchange_order_id
                    evidence.real_exchange_orders += 1
                    evidence.add(
                        ContractStep(
                            "entry_submit_recovery",
                            True,
                            f"recovered exchange_order_id={receipt.exchange_order_id}",
                        )
                    )
            except Exception as recovery_exc:  # noqa: BLE001
                evidence.add(
                    ContractStep(
                        "entry_submit_recovery",
                        False,
                        f"{type(recovery_exc).__name__}: {recovery_exc}",
                    )
                )

        # --- 5. Real fill receipt (bounded polling) ---
        if receipt is not None:
            try:
                fills = _poll_fills(
                    adapter,
                    symbol=symbol,
                    exchange_order_id=receipt.exchange_order_id,
                    expected_quantity=quantity,
                )
                evidence.network_calls += 1
                trade_ids = [fill.trade_id for fill in fills]
                filled_qty = sum((fill.filled_quantity for fill in fills), Decimal("0"))
                filled_notional = sum((fill.filled_quantity * fill.fill_price for fill in fills), Decimal("0"))
                avg_price = (filled_notional / filled_qty) if filled_qty > 0 else None
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
            if contract_direction == "long":
                stop_price = (avg_price * Decimal("0.99")).quantize(market.tick_size)
                target_price = (avg_price * Decimal("1.02")).quantize(market.tick_size)
            else:
                stop_price = (avg_price * Decimal("1.01")).quantize(market.tick_size)
                target_price = (avg_price * Decimal("0.98")).quantize(market.tick_size)
            stop_coid = stop_client_order_id(intent_id)
            target_coid = target_client_order_id(intent_id)
            try:
                stop_receipt, target_receipt = adapter.submit_protection(
                    SubmitProtectionOrders(
                        position_id=intent_id,
                        stop_loss_price=stop_price,
                        take_profit_price=target_price,
                        stop_client_order_id=stop_coid,
                        tp_client_order_id=target_coid,
                    ),
                    symbol,
                    exit_side,
                    filled_qty,
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
                for label, client_order_id in (("stop", stop_coid), ("take_profit", target_coid)):
                    try:
                        recovered = adapter.query_order_by_client_id(symbol, client_order_id)
                        evidence.network_calls += 1
                        if recovered is not None:
                            if label == "stop":
                                evidence.stop_exchange_order_id = recovered.exchange_order_id
                            else:
                                evidence.take_profit_exchange_order_id = recovered.exchange_order_id
                            evidence.add(
                                ContractStep(
                                    f"recover_{label}",
                                    True,
                                    f"recovered exchange_order_id={recovered.exchange_order_id}",
                                )
                            )
                    except Exception as recovery_exc:  # noqa: BLE001
                        evidence.add(
                            ContractStep(
                                f"recover_{label}",
                                False,
                                f"{type(recovery_exc).__name__}: {recovery_exc}",
                            )
                        )

        # --- 7. Normal reduce-only exit ---
        if filled_qty > 0:
            try:
                exit_receipt = adapter.submit_reduce_only_exit(
                    SubmitReduceOnlyExit(
                        position_id=intent_id,
                        exit_reason="testnet_contract_exit",
                        reduce_quantity=filled_qty,
                        client_order_id=exit_client_order_id(intent_id),
                    ),
                    symbol,
                    exit_side,
                )
                evidence.network_calls += 1
                evidence.real_exchange_orders += 1
                evidence.exit_exchange_order_id = exit_receipt.exchange_order_id
                evidence.add(
                    ContractStep("reduce_only_exit", True, f"exchange_order_id={exit_receipt.exchange_order_id}")
                )
                exit_fills = _poll_fills(
                    adapter,
                    symbol=symbol,
                    exchange_order_id=exit_receipt.exchange_order_id,
                    expected_quantity=filled_qty,
                )
                evidence.network_calls += 1
                evidence.exit_trade_ids = [fill.trade_id for fill in exit_fills]
                evidence.add(
                    ContractStep(
                        "exit_fill_receipt",
                        bool(evidence.exit_trade_ids),
                        f"trade_ids={evidence.exit_trade_ids}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                evidence.add(ContractStep("reduce_only_exit", False, f"{type(exc).__name__}: {exc}"))
    finally:
        # --- 8. Contract-scoped cleanup always runs ---
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

        try:
            cleanup_snapshot = adapter.fetch_authoritative_snapshot()
            evidence.network_calls += 1
            remaining_position, _ = _symbol_state(cleanup_snapshot, symbol)
            cleanup_qty = _contract_excess_quantity(
                remaining_position,
                baseline_direction=baseline_direction,
                baseline_quantity=baseline_quantity,
                contract_direction=contract_direction,
            )
            if cleanup_qty > 0 and evidence.entry_exchange_order_id:
                cleanup_receipt = adapter.submit_reduce_only_exit(
                    SubmitReduceOnlyExit(
                        position_id=intent_id,
                        exit_reason="testnet_contract_compensation",
                        reduce_quantity=cleanup_qty,
                        client_order_id=exit_client_order_id(f"{intent_id}-cleanup"),
                        is_emergency=True,
                    ),
                    symbol,
                    exit_side,
                )
                evidence.network_calls += 1
                evidence.real_exchange_orders += 1
                cleanup_fills = _poll_fills(
                    adapter,
                    symbol=symbol,
                    exchange_order_id=cleanup_receipt.exchange_order_id,
                    expected_quantity=cleanup_qty,
                )
                evidence.network_calls += 1
                if evidence.exit_exchange_order_id is None:
                    evidence.exit_exchange_order_id = cleanup_receipt.exchange_order_id
                if not evidence.exit_trade_ids:
                    evidence.exit_trade_ids = [fill.trade_id for fill in cleanup_fills]
                evidence.add(
                    ContractStep(
                        "compensating_reduce_only_exit",
                        bool(cleanup_fills),
                        f"exchange_order_id={cleanup_receipt.exchange_order_id} qty={cleanup_qty}",
                    )
                )
            elif not _position_matches_baseline(
                remaining_position,
                baseline_direction=baseline_direction,
                baseline_quantity=baseline_quantity,
            ):
                current_direction = str(remaining_position.direction) if remaining_position is not None else None
                current_quantity = (
                    Decimal(str(remaining_position.quantity)) if remaining_position is not None else Decimal("0")
                )
                evidence.add(
                    ContractStep(
                        "compensating_reduce_only_exit",
                        False,
                        "refused to alter external baseline mismatch: "
                        f"expected={baseline_direction}:{baseline_quantity} "
                        f"actual={current_direction}:{current_quantity}",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            evidence.add(ContractStep("compensating_reduce_only_exit", False, f"{type(exc).__name__}: {exc}"))

        # --- 9. Final exchange truth: both position and open orders are zero ---
        try:
            final_snapshot = adapter.fetch_authoritative_snapshot()
            evidence.network_calls += 1
            final_position, final_orders = _symbol_state(final_snapshot, symbol)
            final_qty = Decimal(str(final_position.quantity)) if final_position is not None else Decimal("0")
            evidence.final_exchange_position_qty = str(final_qty)
            evidence.final_exchange_open_orders = len(final_orders)
            final_new_orders = [item for item in final_orders if str(item.exchange_order_id) not in baseline_order_ids]
            evidence.final_new_open_orders = len(final_new_orders)
            evidence.add(
                ContractStep(
                    "final_position_baseline_restored",
                    _position_matches_baseline(
                        final_position,
                        baseline_direction=baseline_direction,
                        baseline_quantity=baseline_quantity,
                    ),
                    f"final={getattr(final_position, 'direction', None)}:{final_qty} "
                    f"baseline={baseline_direction}:{baseline_quantity}",
                )
            )
            evidence.add(
                ContractStep(
                    "final_contract_open_orders_zero",
                    not final_new_orders,
                    f"final open orders={len(final_orders)} baseline={len(baseline_order_ids)} "
                    f"new={len(final_new_orders)}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            evidence.add(ContractStep("final_exchange_truth", False, f"{type(exc).__name__}: {exc}"))

    evidence.completed_at = datetime.now(UTC).isoformat()
    evidence.overall_passed = all(step.passed for step in evidence.steps)
    evidence.execution_contract_health = evidence.overall_passed
    # Gate 16 is intentionally never a natural strategy/business proof.
    evidence.natural_strategy_business_recovery = False
    evidence.natural_auto_trading_recovery = False
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
    parser.add_argument("--json", action="store_true", help="Emit the separated contract status as JSON")
    args = parser.parse_args()

    print("=" * 72)
    print("Task 16: Binance Testnet CONTRACT verification")
    print("proof_type=TESTNET_CONTRACT  natural_strategy=false")
    print("=" * 72)

    evidence = run_contract(args.symbol, Decimal(args.notional))
    path = write_evidence(evidence)

    if args.json:
        print(
            json.dumps(
                {
                    "proof_type": evidence.proof_type,
                    "execution_contract_health": evidence.execution_contract_health,
                    "natural_strategy_business_recovery": evidence.natural_strategy_business_recovery,
                    "natural_auto_trading_recovery": evidence.natural_auto_trading_recovery,
                    "overall_passed": evidence.overall_passed,
                    "evidence": str(path),
                },
                sort_keys=True,
            )
        )

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
