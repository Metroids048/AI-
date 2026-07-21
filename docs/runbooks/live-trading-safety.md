# Live Trading Safety Runbook

System: AI Quant Research Platform — Execution Layer
Applies to: Transition from Paper → Live

---

## Overview

This runbook defines the mandatory gates, checks, and operational procedures required before enabling live trading (`BINANCE_AUTO_EXECUTE=true` on mainnet). It also describes the safe operating procedures once live trading is active.

**Current state (2026-07-20): System is Paper/Simulation only. Mainnet is disabled.**

---

## Gate 1 — Strategy Validation (Must Pass Before Live)

The following metrics must be achieved on out-of-sample data before live activation:

| Metric | Minimum Threshold |
|---|---|
| OOS trade count | ≥ 200 independent trades |
| Profit Factor (net of fees+slippage) | ≥ 1.20 |
| Average Expectancy | ≥ +0.10R |
| Max Drawdown (OOS) | ≤ 10% |
| Results stable with ±10% parameter variation | Pass |
| Tested across bull, bear, and range regimes | Pass |

Current status: trend_momentum_v1 has 35–65 OOS trades (BTC/ETH). Sample is insufficient for live activation.

---

## Gate 2 — Execution Verification (Must Pass Before Live)

All of the following must be confirmed before enabling live:

- [ ] Testnet acceptance run completed for exact current execution scope (`["BTC/USDT","ETH/USDT"]`)
  - Note: run `da7edfd9` covered BTC/ETH/SOL (3 symbols). Current scope is 2 symbols. New run required.
- [ ] Order direction mapping verified for both ONE_WAY and HEDGE modes
- [ ] Stop-loss and take-profit confirmed correct on testnet fill
- [ ] Restart recovery tested: kill process mid-fill, verify local state re-syncs to exchange
- [ ] Network timeout handling verified: confirmed no duplicate orders after timeout
- [ ] `execution_ready` flag = `true` in the scheduler state

---

## Gate 3 — Risk Parameters (Must Recalibrate for Live)

Current Paper parameters are intentionally aggressive for sampling. They must be tightened for live:

| Parameter | Paper (Sampling) | Live (Starting) |
|---|---|---|
| `risk_per_trade` | 5% | ≤ 0.25% |
| `max_leverage` | 40x | ≤ 3x |
| `max_position_fraction` | 35% | ≤ 10% |
| `max_portfolio_initial_risk_fraction` | 25% | ≤ 1% |
| `max_total_exposure` | 90% | ≤ 30% |
| Max simultaneous positions | Unlimited | 3 |
| Daily loss limit | 20% | 1.5% |

**Do not copy Paper parameters to Live. They are explicitly forbidden for live trading per AGENTS.md.**

Before live activation, the operator must explicitly approve the live risk parameters in writing (commit to AGENTS.md or a dated decision record).

---

## Gate 4 — Operational Readiness

- [ ] `recovery.md` runbook has been read and understood by the operator
- [ ] Kill switch tested (`kill_switch.py` closes all positions within 60 seconds)
- [ ] Alert channel configured (system sends notifications on position open/close/rejected)
- [ ] Monitoring dashboard shows correct real-time PnL, position count, and account equity
- [ ] Maximum drawdown alert threshold set (alert at 50% of `hard_stop_drawdown_limit`)
- [ ] Operator has manual override access to Binance UI in case of system failure

---

## Live Operation Procedures

### Daily Start

1. Run `verify_runtime_config_sync` to confirm database and running config match.
2. Check open positions from previous session.
3. Verify no orphan orders (orders without corresponding positions).
4. Confirm exchange connectivity.

### During Trading Hours

- Do not manually modify positions while the system is running.
- If you must intervene: set `BINANCE_AUTO_EXECUTE=false` first, then act manually.
- Log all manual interventions in `docs/audits/` with timestamp and reason.

### Daily End

1. Review the day's decision log:
   ```powershell
   agent-python -m scripts.audit_decision_funnel --database-url <url> --lookback-days 1
   ```
2. Check that all blocked decisions have documented reasons.
3. Verify account equity matches expected PnL.
4. Document any anomalies.

---

## Emergency Procedures

### Immediate Stop

If you need to stop all trading immediately:

1. Set `BINANCE_AUTO_EXECUTE=false`
2. Run `kill_switch.py` to close all positions
3. Verify exchange shows zero open positions
4. Document the reason

**Do not just kill the process** — positions may remain open on the exchange without local tracking.

### Runaway Loss

If account drawdown exceeds `hard_stop_drawdown_limit`:

1. System should auto-trigger `kill_switch.py`
2. Verify all positions are closed on the exchange
3. Do not restart until root cause is identified
4. Review the last 20 decisions to identify the failure mode

---

## Transition Sequence: Paper → Live

```
Stage 1: Paper (current)
  → Complete OOS validation (≥200 trades)
  → Tighten risk parameters

Stage 2: Single-symbol, minimum size
  → BTC/USDT only
  → risk_per_trade = 0.001 (0.1%)
  → max_leverage = 3x
  → max_positions = 1
  → Monitor for 30 days

Stage 3: Add ETH/USDT
  → Only after Stage 2 passes for 30 days
  → Verify correlation group risk is enforced

Stage 4: Full production
  → Only after Stage 3 passes
  → Risk parameters reviewed by operator
```

Each stage transition requires a dated decision record in `docs/audits/`.

---

## What Must NOT Happen in Live

- Never copy Paper risk parameters to Live
- Never skip testnet acceptance for a new execution scope
- Never disable stop-loss protection to "hold through a drawdown"
- Never manually override the risk engine mid-session without documenting
- Never run Live and Paper on the same exchange account simultaneously
- Never hardcode API keys in any file
- Never add `--no-verify` to git commits
