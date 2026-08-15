# 2026-08-14 Binance 真实交易审计与策略 Loop

## 用户目标

先拉取 Binance Testnet 可获取的真实成交与资金事实，与本地 V2 决策链逐笔对账，建立 Trade Episode 数据集；仅依据真实亏损归因运行策略研究回放，执行链完全冻结。

## 环境

- `AUDIT_ENVIRONMENT_VERIFIED`
- Binance Testnet read-only connectivity passed.
- Scope: BTC/USDT, ETH/USDT.

## 结果

- Range: `2026-07-07T16:52:55.936Z` to `2026-08-14T07:30:35.847Z`.
- Exchange: 449 trades, 282 orders, 575 income records, 204 algo orders.
- Local V2: 141 fills; matched 141; match rate `1.0`; unmatched records `308`.
- Completeness: `PASS`.
- V2 closed episodes: 25; net PnL `-454.11402372` USDT; commission
  `172.83684269`; funding `-88.23480101`; PF `0.3370`; expectancy
  `-18.16456095`; win rate `48%`; max DD `509.94780122` USDT.
- Top loss cause: `STOP`, 12 episodes, `-675.23621504` USDT.
- Reconciliation defects: 1773; open incidents: 12. Slippage/latency were not
  inferable from immutable persisted references and remain marked unmeasured.

## Strategy Loop

- Added research-only `loss_aware_trend_pullback_v1` based on the observed stop-loss
  failure shape; no production authorization or execution write.
- OOS generation N+1: 1,099 trades, PF `0.7081`, expectancy `-0.001229`, rejected.
- Best nonzero candidate: `trend_pullback_v2`, 405 trades, PF `0.8210`, expectancy
  `-0.0007721`, rejected. Cost stress remained negative through 20 bps/side.

## Final Status

`AUDIT_PASS / STRATEGY_NOT_ACCEPTED / EXECUTION_FROZEN`

## Evidence

See `artifacts/trading_audit/reports/optimization_result.md` and
`artifacts/trading_audit/reports/live_strategy_evaluation.md`.
