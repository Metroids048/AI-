# Recovery Runbook

System: AI Quant Research Platform — Execution Layer
Applies to: Paper mode and Live mode

---

## When to Use This Runbook

Use this runbook when the system is in any of the following states:

- Scheduler has not emitted a heartbeat for > 3 minutes
- Local positions do not match exchange positions
- An order is in `UNKNOWN` or `RECOVERY_REQUIRED` state
- Program crashed after order submission but before fill confirmation
- Stop-loss or take-profit order is missing for an open position
- Database shows open position but exchange shows flat

---

## Step 0 — Stop New Trading Before Investigating

Before touching anything, prevent new orders from opening:

```powershell
# Check current scheduler state
agent-python -m scripts.verify_runtime_config_sync --database-url sqlite:///.local_paper_console.db
```

If `BINANCE_AUTO_EXECUTE=true` is set, set it to `false` in the environment before proceeding.

---

## Step 1 — Determine Current Exchange State (Ground Truth)

Exchange state is always the final authority. Retrieve it before modifying any local state.

```powershell
# Run the reconciliation check
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_paper_console.db --lookback-days 1
```

Manually check on Binance Testnet/Paper:
1. Open positions (symbol, side, quantity, entry price)
2. Open orders (stop-loss, take-profit, any pending entries)
3. Account balance and unrealized PnL

---

## Step 2 — Compare with Local State

Check what the local database believes:

```powershell
agent-python -m scripts.verify_config
```

Identify discrepancies:

| Scenario | Action |
|---|---|
| Exchange has position, local shows flat | Local state drifted — see Section A |
| Local shows position, exchange is flat | Order was rejected or filled and closed — see Section B |
| Open order missing stop-loss | Re-arm protection — see Section C |
| Order stuck in SUBMITTING state | Check for duplicate order — see Section D |

---

## Section A — Exchange Has Position, Local Shows Flat

1. Retrieve the exchange position details (symbol, side, size, entry price, leverage).
2. Create a recovery record in the local database using the admin API or direct DB update.
3. Immediately create a stop-loss order at the appropriate price.
4. Mark the local position as `RECOVERY_REQUIRED` until the stop-loss is confirmed.

**Do not open new positions until the recovered position has a confirmed stop-loss.**

---

## Section B — Local Shows Position, Exchange Is Flat

1. Confirm the exchange is truly flat (check both positions and recent fills).
2. If the position was closed by a stop-loss or take-profit on the exchange, record the fill.
3. Mark the local position as `CLOSED` with `close_reason = exchange_flat_on_recovery`.
4. Calculate realized PnL from the exchange fill price and update local metrics.

---

## Section C — Open Position Missing Stop-Loss

This state is **critical** — the position is unprotected.

1. Do not open any new positions.
2. Calculate the correct stop price:
   - For trend_momentum_v1: use the structural low (long) or structural high (short) from the signal candle.
   - Fallback: use `entry_price × (1 - max_risk_fraction / leverage)`.
3. Place a `STOP_MARKET` with `reduceOnly=True` (one-way mode) or `positionSide=LONG/SHORT` (hedge mode).
4. Confirm the stop order is acknowledged by the exchange.
5. Resume normal operation only after confirmation.

---

## Section D — Order Stuck in SUBMITTING State

This happens after a network timeout where the exchange response was not received.

**Do not create a second order.** The exchange may have already accepted the first one.

1. Query the exchange using the original `clientOrderId`:
   ```
   GET /fapi/v1/order?symbol=BTCUSDT&origClientOrderId=aqrp-<hash>
   ```
2. If the order exists on the exchange: update local state to match.
3. If the order does not exist: it was never created. Re-submit with the same `clientOrderId`.
4. Never generate a new `clientOrderId` for a retry — idempotency requires the same ID.

---

## Step 3 — Restart Checklist

Before allowing the scheduler to resume:

- [ ] All open positions have a confirmed stop-loss on the exchange
- [ ] No orders in `UNKNOWN` or `SUBMITTING` state for > 5 minutes
- [ ] Local position count matches exchange position count
- [ ] `verify_config` script passes with no errors
- [ ] Account equity is correctly reflected in local metrics

---

## Step 4 — Resume

```powershell
# Verify config sync
agent-python -m scripts.verify_runtime_config_sync --database-url sqlite:///.local_paper_console.db

# Run the acceptance check if scope changed
# Trigger new acceptance run for BTC/ETH if da7edfd9 run scope doesn't match current 2-symbol scope
```

Resume the scheduler only after all checklist items pass.

---

## Escalation

If recovery steps above cannot resolve the discrepancy after 15 minutes:

1. Set `BINANCE_AUTO_EXECUTE=false`.
2. Manually close all open positions on the exchange.
3. Document the incident in `docs/audits/` with date, affected positions, and resolution.
4. Do not restart automatic trading until the root cause is identified.
