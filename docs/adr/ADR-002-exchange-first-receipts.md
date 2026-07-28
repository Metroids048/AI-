# ADR-002: Exchange-First Receipts

- **Date:** 2026-07-28
- **Status:** Accepted
- **Supersedes:** Legacy mixed local/exchange order state management

## Context

The legacy system created local `OrderExecution` records with states like `accepted`, `filled`, and `completed` before confirming exchange interaction. This led to several failure modes:

1. **Ghost Positions:** Local state advanced to `filled` even when the exchange never received the order, or received it but rejected it, or the network call timed out before acknowledgment.
2. **Stale Reference Prices:** Stop-loss and take-profit prices were calculated from signal candle close prices, which could drift significantly from actual fill prices by the time the order executed.
3. **Recovery Ambiguity:** On restart, the system could not distinguish between "order pending at exchange," "order lost in transit," and "order never sent."
4. **Proof Gap:** No immutable record of what the exchange actually said, only what the local system interpreted.

## Decision

V2 establishes **exchange receipts as the authoritative source** for all execution facts.

### Receipt Types

#### ExchangeOrderReceipt

Created when the exchange acknowledges an order:

```python
class ExchangeOrderReceipt(FrozenModel):
    account_id: str
    symbol: str
    client_order_id: str
    exchange_order_id: str
    status: str  # NEW, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED
    requested_quantity: Decimal
    acknowledged_at: datetime
    raw_hash: str  # SHA256 of raw exchange response for audit
```

**Uniqueness:** `(account_id, exchange_order_id)` and `(account_id, client_order_id)` are both unique.

#### ExchangeFillReceipt

Created for each trade execution event:

```python
class ExchangeFillReceipt(FrozenModel):
    account_id: str
    exchange_order_id: str
    trade_id: str  # Exchange-assigned trade ID
    filled_quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    commission_asset: str
    exchange_event_time: datetime
    received_at: datetime
    raw_hash: str
```

**Uniqueness:** `(account_id, trade_id)` is unique. Multiple fills for one order → multiple receipts.

**Average Fill Price:** Repository aggregates all fill receipts for an order to compute weighted average; callers must not hand-write average fill price.

### State Machine Integration

Local order state transitions are gated by receipt existence:

```
INTENT_CREATED
  → PRETRADE_APPROVED (risk gates pass)
  → SUBMITTING (network call initiated)
  → ACKNOWLEDGED (ExchangeOrderReceipt received)
  → PARTIALLY_FILLED (first ExchangeFillReceipt received, order still open)
  → FILLED (ExchangeFillReceipt(s) cover full quantity, order closed)
  → POSITION_PROJECTED (local Managed Position created from fill receipts)
```

**Forbidden transitions:**
- `INTENT_CREATED → FILLED` (no exchange interaction)
- `ACKNOWLEDGED → POSITION_PROJECTED` (skips fill proof)
- `SUBMITTING → FILLED` after timeout (must go through `EXCHANGE_UNKNOWN` and recovery)

### Managed Position Invariants

A `BINANCE_TESTNET` mode `Managed Position` in status `OPEN` must satisfy:

```python
assert position.entry_fill_receipt_id is not None
assert position.exchange_entry_order_id is not None
assert position.quantity > 0
assert position.average_entry_price > 0
```

The database enforces these as `NOT NULL` constraints for `ownership_status=MANAGED`.

### Protection Price Calculation

Stop-loss and take-profit absolute prices are computed **after fill**, not before:

```python
# Strategy outputs relative distances
candidate = TradeCandidate(
    stop_distance=Decimal("50.00"),       # $50 below entry
    take_profit_distance=Decimal("150.00"),  # $150 above entry
    max_entry_drift_bps=Decimal("20"),    # max 20bps drift from signal
)

# After Binance confirms fill
fill_receipt = repository.get_fill_receipts(exchange_order_id)
average_fill_price = repository.compute_average_fill_price(fill_receipt)

# THEN calculate protection
stop_price = (average_fill_price - candidate.stop_distance) if side == LONG
             else (average_fill_price + candidate.stop_distance)
take_profit_price = (average_fill_price + candidate.take_profit_distance) if side == LONG
                    else (average_fill_price - candidate.take_profit_distance)

# Submit protection with real prices to exchange
protection_order = submit_stop_loss(exchange_order_id=<real ID>, trigger_price=stop_price)
```

### Recovery Protocol

When an order is in `EXCHANGE_UNKNOWN` state (network timeout after submission):

1. Query exchange by `client_order_id` (deterministic, replay-safe)
2. Query recent orders for the account
3. Query recent trades for the symbol
4. If found → restore state from exchange receipts
5. If multiple consecutive snapshots confirm absent → mark `NOT_FOUND_CONFIRMED`
6. Only then may Recovery Service decide whether to retry

**Forbidden:** Blindly resubmitting with a new `client_order_id` without confirmation the first order was never received.

## Consequences

### Positive

- **No Ghost Positions:** Local `Managed Position` existence proves exchange execution occurred.
- **Auditable:** Every execution fact traces to an immutable receipt with `raw_hash` of exchange response.
- **Accurate Protection:** Stop/TP based on actual fill price, not stale signal price.
- **Deterministic Recovery:** `client_order_id` allows safe query without creating duplicate orders.
- **Partial Fill Handling:** System projects only confirmed quantity, not requested quantity.

### Negative

- **Latency:** Cannot optimistically project position before exchange acknowledgment.
- **Storage:** Every trade produces a separate receipt row; high-frequency strategies accumulate data.

### Mitigation

- Receipt rows are append-only; no update churn.
- Aggregation (average price, total quantity) computed on-demand by repository, not stored redundantly.
- Local `INTENT_CREATED` → `PRETRADE_APPROVED` still happens immediately for UX; only `FILLED` requires exchange proof.

## Verification

```bash
pytest tests/services/test_automated_trading_repository.py -k receipt
pytest tests/services/test_automated_trading_entry.py -k "fill_receipt"
pytest tests/contracts/test_automated_trading_contracts.py -k "no_receipt_no_position"
```

Evidence must show:
- Testnet mode without `ExchangeFillReceipt` → repository rejects `POSITION_PROJECTED` transition
- Partial fill → position quantity == sum of fill receipts, not requested quantity
- Average fill price computed from multiple trade IDs, not user-supplied
