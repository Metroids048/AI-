# 五候选策略公平竞赛报告

- Generated: 2026-07-19T12:07:02.244444+00:00
- Data: stored BTC/USDT, ETH/USDT, SOL/USDT OHLCV; 365-day 70/30 chronological split
- Scope: offline replay only; this report does not alter active execution configuration.
- CI: 90% bootstrap confidence intervals (1000 resamples, percentile method).

| candidate | symbol | samples | OOS samples | OOS win rate | OOS net expectancy | expectancy CI 90% | Sharpe | Sharpe CI 90% | PF | Max DD | failed reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| pandas_ta_broad_screen_v1 | ETH/USDT | 4 | 1 | 1.0000 | 0.048800 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 9.9900 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1 |
| operator_heuristic_v2_relaxed | BTC/USDT | 218 | 29 | 0.4483 | 0.008288 | [-0.002924, 0.019501] | 2.2910 | [-0.8571, 5.6041] | 1.6100 | 0.1338 | insufficient_oos_trades |
| trend_momentum_v1 | BTC/USDT | 204 | 35 | 0.4571 | 0.006984 | [-0.002629, 0.017698] | 2.1054 | [-0.8355, 5.3801] | 1.4910 | 0.1798 | none |
| operator_heuristic_v1 | BTC/USDT | 199 | 28 | 0.4286 | 0.006841 | [-0.003873, 0.018454] | 1.8667 | [-1.1508, 5.1844] | 1.4861 | 0.1385 | insufficient_oos_trades |
| trend_breakout_v1 | BTC/USDT | 209 | 35 | 0.4286 | 0.006657 | [-0.004057, 0.017371] | 2.0252 | [-1.3458, 5.3149] | 1.4669 | 0.1460 | none |
| trend_momentum_v1 | ETH/USDT | 525 | 65 | 0.4462 | 0.006569 | [-0.000815, 0.013953] | 2.6352 | [-0.3403, 5.6087] | 1.4527 | 0.1653 | none |
| operator_heuristic_v2_relaxed | ETH/USDT | 573 | 60 | 0.4000 | 0.003800 | [-0.003700, 0.011300] | 1.4861 | [-1.5469, 4.3298] | 1.2417 | 0.1974 | profit_factor_not_above_1_3 |
| operator_heuristic_v1 | ETH/USDT | 474 | 60 | 0.3833 | 0.002550 | [-0.006200, 0.010050] | 1.0048 | [-2.6861, 3.8530] | 1.1578 | 0.2080 | profit_factor_not_above_1_3 |
| operator_heuristic_v1 | XRP/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v1 | BNB/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v1 | DOGE/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v1 | ADA/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v1 | TRX/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_momentum_v1 | XRP/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_momentum_v1 | BNB/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_momentum_v1 | DOGE/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_momentum_v1 | ADA/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_momentum_v1 | LINK/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_momentum_v1 | TRX/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | XRP/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | DOGE/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | TRX/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | XRP/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | BNB/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | DOGE/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | ADA/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | LINK/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | AVAX/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | TRX/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | SOL/USDT | 2 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | XRP/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | DOGE/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | ADA/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | TRX/USDT | 0 | 0 | 0.0000 | 0.000000 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0000 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | ETH/USDT | 437 | 61 | 0.3443 | -0.000380 | [-0.007757, 0.006997] | -0.1534 | [-3.4516, 2.6990] | 0.9779 | 0.2039 | sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | ADA/USDT | 1 | 1 | 0.0000 | -0.003423 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0034 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | BNB/USDT | 1 | 1 | 0.0000 | -0.004604 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0046 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | BNB/USDT | 1 | 1 | 0.0000 | -0.004604 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0046 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | SOL/USDT | 553 | 114 | 0.2632 | -0.006463 | [-0.011726, -0.001200] | -3.8394 | [-7.7728, -0.6659] | 0.6652 | 0.8481 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| trend_momentum_v1 | SOL/USDT | 486 | 106 | 0.2547 | -0.006873 | [-0.012310, -0.001920] | -3.9768 | [-8.0866, -1.0377] | 0.6440 | 0.7866 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| operator_heuristic_v1 | SOL/USDT | 440 | 110 | 0.2545 | -0.007109 | [-0.012564, -0.002336] | -4.1893 | [-8.3617, -1.2876] | 0.6360 | 0.8653 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| trend_breakout_v1 | SOL/USDT | 497 | 108 | 0.2500 | -0.007234 | [-0.012574, -0.001463] | -4.2513 | [-8.3989, -0.7979] | 0.6278 | 0.7942 | sharpe_not_above_1, profit_factor_not_above_1_3, max_drawdown_not_below_25pct, net_expectancy_not_positive |
| operator_heuristic_v1 | LINK/USDT | 1 | 1 | 0.0000 | -0.009927 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0099 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | LINK/USDT | 1 | 1 | 0.0000 | -0.009927 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0099 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | LINK/USDT | 1 | 1 | 0.0000 | -0.009927 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0099 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_momentum_v1 | AVAX/USDT | 2 | 2 | 0.0000 | -0.022523 | [-0.026600, -0.018445] | -487.6046 | [-487.6046, 0.0000] | 0.0000 | 0.0450 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| pandas_ta_broad_screen_v1 | BTC/USDT | 1 | 1 | 0.0000 | -0.026200 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0262 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v1 | AVAX/USDT | 1 | 1 | 0.0000 | -0.026600 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0266 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| trend_breakout_v1 | AVAX/USDT | 1 | 1 | 0.0000 | -0.026600 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0266 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |
| operator_heuristic_v2_relaxed | AVAX/USDT | 1 | 1 | 0.0000 | -0.026600 | [0.000000, 0.000000] | 0.0000 | [0.0000, 0.0000] | 0.0000 | 0.0266 | insufficient_oos_trades, sharpe_not_above_1, profit_factor_not_above_1_3, net_expectancy_not_positive |

## ⚠️ 小样本警告

- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / ETH/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / BTC/USDT 仅 29 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / BTC/USDT 仅 28 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / XRP/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / BNB/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / DOGE/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / ADA/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / TRX/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_momentum_v1 / XRP/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_momentum_v1 / BNB/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_momentum_v1 / DOGE/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_momentum_v1 / ADA/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_momentum_v1 / LINK/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_momentum_v1 / TRX/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_breakout_v1 / XRP/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_breakout_v1 / DOGE/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_breakout_v1 / TRX/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / XRP/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / BNB/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / DOGE/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / ADA/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / LINK/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / AVAX/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / TRX/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / SOL/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / XRP/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / DOGE/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / ADA/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / TRX/USDT 仅 0 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_breakout_v1 / ADA/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_breakout_v1 / BNB/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / BNB/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / LINK/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_breakout_v1 / LINK/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / LINK/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_momentum_v1 / AVAX/USDT 仅 2 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：pandas_ta_broad_screen_v1 / BTC/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v1 / AVAX/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：trend_breakout_v1 / AVAX/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
- ⚠️ 小样本警告：operator_heuristic_v2_relaxed / AVAX/USDT 仅 1 笔 OOS 交易，置信区间较宽，建议积累更多样本后复核。
