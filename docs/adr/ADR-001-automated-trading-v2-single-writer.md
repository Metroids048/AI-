# ADR-001: Automated Trading V2 Single Writer

- **Date:** 2026-07-28
- **Status:** Accepted
- **Supersedes:** Legacy `binance_simulation_first` / `mirror_to_gateway` semantics

## Context

The legacy automatic trading system allowed multiple conceptual "modes" to coexist:
- `LOCAL_PAPER` (pure simulation)
- `binance_simulation_first` (Testnet if available, local fill as fallback)
- `mirror_to_gateway` (send to Testnet but also create local state)

This created ambiguity:
1. When Testnet was unavailable, local orders could remain `accepted` indefinitely.
2. A local `fill` event could create a `MANAGED_STRATEGY` position even when the exchange never acknowledged the order.
3. Two different code paths could attempt to write orders to Binance Testnet simultaneously during transitions or recovery.

Ghost positions (local managed positions with no corresponding exchange position) were the direct result of this semantic mixing.

## Decision

V2 enforces **one and only one active Testnet order writer** at any time.

### Engine Activation States

```python
class EngineActivation(StrEnum):
    DISABLED = "DISABLED"  # No evaluation, no submission
    SHADOW = "SHADOW"      # Read data, generate candidates, run gates, but do not submit orders
    ACTIVE = "ACTIVE"      # Exclusive permission to submit Testnet orders
```

### Runtime Modes

```python
class AutomatedTradingMode(StrEnum):
    LOCAL_PAPER = "LOCAL_PAPER"      # Pure simulation, no exchange interaction
    BINANCE_TESTNET = "BINANCE_TESTNET"  # Exchange-first execution on Binance Simulation
```

**Forbidden modes (deleted from V2 enums):**
- `binance_simulation_first`
- `mirror_to_gateway`
- `testnet-but-local-fill`

### Single Writer Contract

1. At most one V2 engine may be in `ACTIVE` state at any time.
2. `ACTIVE` state is gated by:
   - Runtime lock acquisition (using existing scheduler fencing)
   - Explicit Testnet authorization arming (from prior OOS validation)
   - Healthy reconciliation
   - Deployment version match
3. When V2 is `SHADOW`, it must not call any order submission API.
4. When V2 is `ACTIVE`, legacy pipeline Testnet write call sites must be unreachable.
5. Rollback only disables V2 entry; it does not re-activate legacy writers.

### Transition Protocol

**Before cutover:**
- Legacy system continues with existing Testnet authorization
- V2 runs in `SHADOW` mode for validation

**Cutover:**
1. Stop legacy scheduler new entries
2. Wait for all legacy positions to close naturally via legacy exit path
3. Query Binance for authoritative account state
4. Confirm zero open positions, zero active orders
5. Set `AUTOMATED_TRADING_ENGINE=v2_active`
6. V2 bootstrap performs recovery/reconciliation before first entry
7. Delete or disable legacy Testnet submit call sites

**Rollback (if needed):**
- Set V2 `activation=DISABLED`
- V2 exit/recovery paths continue managing any open V2 positions to closure
- Legacy writer remains disabled; rollback is not re-activation of dual writers

## Consequences

### Positive

- No more ghost positions from local-fill fallback
- Clear proof type for every order: `STRICT_FAKE`, `SHADOW`, `TESTNET_CONTRACT`, `NATURAL_SCHEDULER_TESTNET`
- Reconciliation failures can safely block entry without ambiguity
- Cutover is a deterministic state transition, not a gradual feature-flag migration

### Negative

- Cannot run "Testnet preferred, local fallback" for unattended high-availability; operator must choose one mode
- Requires explicit cutover ceremony with evidence bundle
- Rollback is one-way (V2 off), not bidirectional toggle

### Mitigation

- `LOCAL_PAPER` mode remains fully supported for offline research and deterministic replay
- Testnet Sampling lane provides high-frequency execution testing without promoting results to strategy晋升
- Natural scheduler E2E is the only gate that proves the real pipeline works; acceptance往返单 and Fake adapters are clearly labeled

## Verification

```bash
pytest tests/services/test_automated_trading_engine_activation.py -v
pytest tests/contracts/test_single_testnet_writer_after_cutover.py -v  # Created in Task 18
```

Evidence must show:
- Two engines attempting `ACTIVE` at once → second rejected
- `SHADOW` engine generates normalized orders but `network_order_submit_calls == 0`
- After cutover, legacy Orchestrator Testnet paths unreachable (import guard or deleted)
