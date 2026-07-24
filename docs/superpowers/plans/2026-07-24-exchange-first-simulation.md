# Exchange-First Binance Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Binance Demo/Testnet the authoritative execution source for the BTC/ETH automated lane, with SQLite acting only as a post-acknowledgement projection and audit ledger.

**Architecture:** Keep the existing scheduler, strategy, Gatekeeper, fixed position, fixed leverage, and protection rules unchanged. Change execution semantics and defaults so authorized automatic runs submit to Binance first, persist authoritative exchange fill details, and only then create/update local order and position projections. Retain local-only Paper solely as an explicit offline testing mode.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy/SQLite, CCXT Binance USDM gateway, pytest, Ruff, Mypy.

## Global Constraints

- Automatic execution symbols remain exactly `BTC/USDT` and `ETH/USDT`.
- Existing fixed position, leverage, stop-loss, take-profit, Gatekeeper, net-edge, and strategy parameters must not change.
- Mainnet remains disabled; exchange-first automation is permitted only when `BINANCE_USE_TESTNET=true`, `LIVE_TRADING_ENABLED=false`, credentials exist, and `BINANCE_AUTO_EXECUTE=true`.
- Testnet acceptance and cost-gate authorization remain mandatory; this change must not bypass them.
- Local SQLite data is a projection of exchange acknowledgements/fills, not an execution authority.
- Original archive `/mnt/data/AI--main.zip` remains untouched; deliver a separately named ZIP.

---

### Task 1: Record the exchange-first invariant

**Files:**
- Modify: `AGENTS.md`
- Modify: `CURRENT_STATE.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: Current BTC/ETH execution scope and existing testnet safety settings.
- Produces: A durable project rule that future agents must follow.

- [ ] Add an `Exchange-First Execution Invariant` section to `AGENTS.md` stating that Binance Simulation is the execution truth source and SQLite is only a projection.
- [ ] Correct `CURRENT_STATE.md` so it no longer presents local Paper fills as proof of execution and describes BTC/ETH exchange-first behavior.
- [ ] Set and document the simulation default in `.env.example` without enabling mainnet.
- [ ] Verify exact wording with `rg` and re-read changed sections.

### Task 2: Make safe testnet execution the default for authorized automatic runs

**Files:**
- Modify: `tests/services/test_paper_bootstrap.py`
- Modify: `services/execution/bootstrap.py`
- Modify: `shared/config.py`

**Interfaces:**
- Consumes: `settings.binance_auto_execute`, credentials, testnet/mainnet guards.
- Produces: `default_mirror_to_gateway() -> bool` that is true only for safe, credentialed testnet execution.

- [ ] Replace the existing test that always expects `False` with a test matrix proving the function returns `True` only for safe testnet conditions.
- [ ] Run the focused test and confirm it fails under the current implementation.
- [ ] Implement the minimal safe predicate in `default_mirror_to_gateway()`.
- [ ] Change the settings default to exchange-first simulation while preserving mainnet guards.
- [ ] Run bootstrap tests and confirm directional runs default to `binance_simulation_first` only when authorized.

### Task 3: Persist authoritative exchange fill details

**Files:**
- Modify: `tests/services/test_binance_gateway.py`
- Modify: `services/execution/gateway.py`
- Modify: `tests/services/test_position_identity.py`
- Modify: `services/execution/paper_exchange_execution.py`
- Modify: `services/execution/paper_order_lifecycle.py`

**Interfaces:**
- Consumes: Raw CCXT `create_order`/`fetch_order` payloads.
- Produces: `average_fill_price`, `filled_quantity`, `fill_timestamp`, `fill_source`, and `exchange_fill_confirmed` in the gateway result/order entry context.

- [ ] Add a failing gateway test where a filled market order returns actual average price and filled quantity.
- [ ] Add a failing lifecycle test proving local position entry price and quantity use exchange fill fields rather than strategy reference values.
- [ ] Run both tests and confirm failures are caused by missing exchange fill propagation.
- [ ] Implement robust extraction of average fill price, filled quantity, and timestamp; fetch the order once when a filled response lacks details.
- [ ] Persist those fields through `ensure_binance_execution()`.
- [ ] Update local position projection to use exchange fill details when confirmed.
- [ ] Keep local-only explicit Paper behavior using reference values.
- [ ] Run focused gateway, order-context, and position-identity tests.

### Task 4: Use exchange fill prices for local close projection and PnL

**Files:**
- Modify: `tests/services/test_paper_runtime.py`
- Modify: `services/execution/paper_cycle_orchestrator.py`

**Interfaces:**
- Consumes: `OrderExecution.entry_context["exchange_average_fill_price"]` from Task 3.
- Produces: Local close snapshots and realized PnL based on the confirmed Binance fill price.

- [ ] Add a failing test showing a Binance close filled at a price different from the trigger/reference price and asserting local PnL uses the exchange fill.
- [ ] Run the test and confirm current code uses the trigger/reference price.
- [ ] Add one helper that selects confirmed exchange fill price with explicit fallback only for local-only Paper.
- [ ] Apply the helper to all automatic close paths after successful Binance execution.
- [ ] Run focused runtime and protection tests.

### Task 5: Verify no local position is created before exchange confirmation

**Files:**
- Modify: `tests/services/test_paper_runtime.py`
- Modify: `services/execution/paper_cycle_orchestrator.py` only if required by failing test

**Interfaces:**
- Consumes: Gateway execution status and fill confirmation fields.
- Produces: No local open position when Binance reports an unfilled/submitted entry.

- [ ] Add a regression test for an acknowledged/open Binance entry.
- [ ] Assert the scheduler records `pending_gateway_fill` and local position remains flat.
- [ ] Run the test; retain current behavior if already correct.
- [ ] Make only the minimal implementation change if the regression test exposes a gap.

### Task 6: Full verification and packaging

**Files:**
- Review all changed files.
- Create: `/mnt/data/AI--main-exchange-first-fixed.zip`

**Interfaces:**
- Consumes: Completed Tasks 1-5.
- Produces: Verified replacement ZIP while preserving the original archive.

- [ ] Run focused exchange-first tests.
- [ ] Run `ruff check .`.
- [ ] Run `mypy` using the project command.
- [ ] Run `pytest -q` and report all pre-existing failures separately from new failures.
- [ ] Re-read every changed critical code section.
- [ ] Generate a file diff/stat comparison against the untouched extracted baseline.
- [ ] Repack the modified project into a new ZIP and verify its contents and checksum.
