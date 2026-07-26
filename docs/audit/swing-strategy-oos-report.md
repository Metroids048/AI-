# 1d/4h Swing Strategy OOS Validation Report

**Generated**: 2026-07-26T04:39:57.447335+00:00

**Strategy**: `auto_paper_swing_1d_4h`

**Timeframes**: Direction=1d, Entry=4h, State=1d

**Validation Period**: 2026-04-27 to 2026-07-26 (90 days)

**Symbols**: BTC/USDT, ETH/USDT

## Metrics

| Metric | Value | Gate | Status |
|--------|-------|------|--------|
| Total Trades | 11 | ≥ 20 | ❌ |
| Win Rate | 54.55% | - | - |
| Sharpe Ratio | 1.326 | > 1.0 | ✅ |
| Profit Factor | 1.423 | > 1.3 | ✅ |
| Max Drawdown | 13.96% | < 25% | ✅ |
| Expectancy | 0.0081 | > 0 | ✅ |

## ❌ Validation Result: REJECTED

The strategy did NOT pass all validation gates:

- ❌ Insufficient sample size (11 < 20 trades)

**Decision**: Keep `auto_schedule_enabled=False` and do NOT wire into `bootstrap_local_paper_runtime()`. Strategy remains disabled until further optimization or longer data accumulation.
