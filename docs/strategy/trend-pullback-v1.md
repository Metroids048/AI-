# Strategy: trend_pullback_v1 / trend_momentum_v1

Version: 1.0
Status: Active (Paper, BTC/USDT + ETH/USDT)
Last updated: 2026-07-20

---

## 1. Core Thesis

Buy pullbacks in established uptrends; sell rallies in established downtrends. The trade seeks R/R ≥ 2:1 at a structural level, with the trend providing directional bias and the pullback providing favorable entry positioning.

This is not a prediction strategy. It bets that in trending markets, mean reversion toward the trend is statistically more likely than continuation of the pullback.

---

## 2. Timeframe Hierarchy

| Purpose | Timeframe |
|---|---|
| Market regime (global filter) | 1h (BTC as proxy) |
| Symbol trend filter | 1h |
| Entry signal | 15m |
| Position monitoring | 1m (stop-loss/take-profit checks only) |

---

## 3. Market Regime Filter

Required before any entry is considered:

| Regime | Entry Allowed |
|---|---|
| BULL | LONG candidates only |
| BEAR | SHORT candidates only |
| RANGE | No new entries |
| HIGH_VOLATILITY | No new entries |
| UNTRADABLE | No new entries |

Regime is determined by BTC 1h trend. A non-BTC symbol may not enter if BTC's regime is RANGE or worse, regardless of its own trend.

---

## 4. Entry Conditions

### Hard Gates (all must pass — any single failure blocks entry)

1. **Data valid**: latest closed candle is confirmed closed by exchange server time
2. **Data fresh**: candle age < `max_data_age_seconds` (default: 120s for 15m bars)
3. **No gap**: no missing candles in the required window
4. **Market tradable**: regime is BULL (long) or BEAR (short)
5. **Spread acceptable**: bid-ask spread < `max_spread_bps` (default: 10 bps)
6. **No duplicate signal**: same strategy + symbol + candle_close_time has not been acted on
7. **No state conflict**: no open position in the same symbol in the same direction
8. **Portfolio risk within limits**: total portfolio initial risk < `max_portfolio_initial_risk_fraction`
9. **Net RR sufficient**: after fee and slippage, expected R/R ≥ `min_net_rr` (default: 1.8)
10. **No correlation conflict**: no high-correlation peer position in the opposite direction

### Soft Score (total 0–100; threshold ≥ 70 for entry)

| Component | Weight | Description |
|---|---|---|
| Trend quality | 0–35 | EMA alignment, ADX, slope |
| Pullback location | 0–25 | Distance from structure, Fibonacci level |
| Momentum recovery | 0–20 | RSI crossing from oversold/overbought, MACD crossover |
| Volume confirmation | 0–10 | Volume not collapsing during pullback |
| Relative strength | 0–10 | Symbol outperforming (LONG) or underperforming (SHORT) peer group |

Score < 70: no entry, log score breakdown as `score_below_threshold` blocker.

---

## 5. Position Sizing

Fixed fractional risk:

```
risk_amount = account_equity × risk_per_trade
stop_distance = abs(entry_price - stop_price)
raw_quantity = risk_amount / stop_distance / contract_multiplier
quantity = floor(raw_quantity, step_size)
```

If `notional < min_notional` after sizing: entry blocked (`ORDER_BELOW_MINIMUM`).

Paper parameters (current, high-density sampling):
- `risk_per_trade` = 5% (forbidden for live)
- `max_leverage` = 40x (forbidden for live)

Live starting parameters: see `live-trading-safety.md`.

---

## 6. Stop-Loss Placement

Stop is set at the structural level that invalidates the trade thesis:

- **Long**: below the swing low that the price is pulling back from
- **Short**: above the swing high that the price is rallying back from

ATR floor: `stop_distance = max(structural_distance, atr_multiplier × ATR_15m)`

Stop is always placed at a level that the trade thesis can be clearly proven wrong, not at an arbitrary bps distance from entry.

Stop is created as `STOP_MARKET` with `workingType=MARK_PRICE` and `reduceOnly=True` (one-way) immediately after the entry fill is confirmed.

---

## 7. Take-Profit Placement

Version 1: fixed 2R
`take_profit_price = entry_price ± 2 × stop_distance`

Take profit is created as `TAKE_PROFIT_MARKET` with `workingType=MARK_PRICE` and `reduceOnly=True` immediately after the entry fill is confirmed.

Multi-target take-profit (e.g., 1.5R, 2.5R, 3R) is deferred to v2 after v1 baseline is validated.

---

## 8. Exit Conditions

| Condition | Action |
|---|---|
| Stop-loss triggered | Close at stop price |
| Take-profit triggered | Close at target price |
| Market regime changes from BULL/BEAR | Close or tighten stop at next 15m close |
| Time exit: 8 candles without progress | Close at market |
| Hard drawdown lock | Close immediately at market |

---

## 9. Correlation and Portfolio Rules

- BTC and ETH are treated as a correlated cluster (rolling 96-bar 1h correlation typically > 0.75).
- Opening BTC long + ETH short simultaneously is blocked (direction conflict in same cluster).
- Total initial risk across all positions: ≤ `max_portfolio_initial_risk_fraction`.
- Cluster risk: ≤ `max_cluster_risk_fraction`.

---

## 10. LLM Role

LLM is advisory and non-blocking:

- Produces a `context_score` and `confidence` field from news/sentiment analysis.
- LLM output is logged but does not block or override the risk engine.
- If LLM is unavailable, the system continues with `context_score = None`.
- LLM output with `confidence < 0.5` is ignored.

---

## 11. Validation Thresholds (Paper → Live Gate)

| Metric | Minimum |
|---|---|
| OOS trades | ≥ 200 |
| Profit Factor (net) | ≥ 1.20 |
| Expectancy | ≥ +0.10R |
| Max Drawdown (OOS) | ≤ 10% |

Current OOS status (2026-07-19 five-candidate competition):
- BTC: 35 trades, Expectancy +0.0070R, Sharpe 2.1, PF 1.49, MaxDD 18%
- ETH: 65 trades, Expectancy +0.0066R, Sharpe 2.6, PF 1.45, MaxDD 16.5%

**Sample is too small for live activation. 90% confidence intervals include zero.**

---

## 12. Known Limitations

1. The signal pipeline currently runs a multi-indicator ensemble (MACD, EMA, RSI, etc.) rather than the clean single-strategy architecture described in the refactoring plan. This is a technical debt item — the execution is functionally equivalent but harder to reason about.

2. CandleValidator is implemented but not yet wired into the decision pipeline hot path. Bar freshness is partially enforced by the data ingestion layer but not explicitly gate-checked at signal generation.

3. REPLAY and SHADOW modes have enum definitions but no dedicated runtime entry points. Paper mode is used for validation.

---

## 13. Strategy Evolution Path

```
v1 (current): Fixed 2R, single entry, hard gates + soft score ensemble
v2: MAE/MFE-informed trailing stop (data collected but not activated — see 2026-07-18 report)
v3: Multi-timeframe confirmation with earlier entry (operator_heuristic_v2_relaxed candidate)
```

No v2+ feature is activated until v1 produces ≥ 200 OOS trades with passing metrics.
