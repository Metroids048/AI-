# 五候选策略公平竞赛报告

- Generated: 2026-07-18T06:40:41.551794+00:00
- Data: stored BTC/USDT, ETH/USDT, SOL/USDT OHLCV; 365-day 70/30 chronological split
- Scope: offline replay only; this report does not alter active execution configuration.

| candidate | symbol | samples | OOS samples | OOS win rate | OOS net expectancy | Sharpe | PF | Max DD | failed reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| pandas_ta_broad_screen_v1 | ETH/USDT | 4 | 1 | 1.0000 | 0.048800 | 0.0000 | 9.9900 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1 |
| operator_heuristic_v2_relaxed | BTC/USDT | 217 | 28 | 0.4643 | 0.009152 | 2.4877 | 1.6777 | 0.1306 | insufficient_oos_trades |
| operator_heuristic_v1 | BTC/USDT | 197 | 27 | 0.4444 | 0.007684 | 2.0582 | 1.5486 | 0.1352 | insufficient_oos_trades |
| trend_breakout_v1 | BTC/USDT | 208 | 34 | 0.4412 | 0.007186 | 2.1520 | 1.5010 | 0.1382 | none |
| trend_momentum_v1 | BTC/USDT | 205 | 35 | 0.4286 | 0.006638 | 2.0127 | 1.4650 | 0.1798 | none |
| trend_momentum_v1 | ETH/USDT | 538 | 64 | 0.4375 | 0.006613 | 2.6488 | 1.4487 | 0.1653 | none |
| operator_heuristic_v2_relaxed | ETH/USDT | 594 | 60 | 0.4000 | 0.003800 | 1.4861 | 1.2417 | 0.1974 | profit_factor_not_above_1_3 |
| operator_heuristic_v1 | ETH/USDT | 482 | 60 | 0.3833 | 0.002550 | 1.0048 | 1.1578 | 0.2080 | profit_factor_not_above_1_3 |
| pandas_ta_broad_screen_v1 | SOL/USDT | 2 | 0 | 0.0000 | 0.000000 | 0.0000 | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | ETH/USDT | 460 | 62 | 0.3387 | -0.000548 | -0.2241 | 0.9679 | 0.1955 | sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | SOL/USDT | 556 | 114 | 0.2632 | -0.006415 | -3.8296 | 0.6669 | 0.8481 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| trend_momentum_v1 | SOL/USDT | 488 | 106 | 0.2642 | -0.006738 | -3.9171 | 0.6505 | 0.7846 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| operator_heuristic_v1 | SOL/USDT | 441 | 110 | 0.2545 | -0.006955 | -4.1259 | 0.6411 | 0.8653 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| trend_breakout_v1 | SOL/USDT | 497 | 108 | 0.2593 | -0.007102 | -4.1932 | 0.6341 | 0.7915 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | BTC/USDT | 1 | 1 | 0.0000 | -0.026200 | 0.0000 | 0.0000 | 0.0262 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
