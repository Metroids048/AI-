# Market Alpha + Meta-Label 研究收口

## 目标

把前三轮基于 OHLCV/结构/趋势的策略命名迭代，替换成新的信息源、概率化交易门控、等风险回放和真实 Forward 验收；V2 Execution 保持冻结。

## 实施

- 新增 `services/strategy_library/market_alpha.py`：衍生品主动买卖压力、现货/永续错位、Funding 压力、BTC/ETH 联动特征。
- 新增只读 runner `scripts/run_market_alpha_meta_research.py`。
- 使用 Binance 公共 Spot/Perp 1h Kline、point-in-time Funding、已有连续 15m 回放数据。
- 评估 5 个 feature set、Logistic/GBM、4 个 target RR，共 40 个 arm。
- 修正时间切分：train/validation/OOS 按 event time 排序；阈值选择只依据验证集 expected Net-R after cost 和 15 笔最低样本，不提前套用最终晋级门槛。
- 最终 acceptance 补齐 payoff、PF、expectancy、LCB95、positive windows、max drawdown、1.5x cost stress。

## 真实结果

- Events: `37,856`
- Development: `2023-01-29` 到 `2026-01-29` sealed boundary
- Holdout: `holdout_accessed=false`
- Configured arms: `6`
- Arms with OOS trades: `3`
- Accepted arms: `0`
- 最佳非零 arm: `BTC_ETH_LEAD_LAG / GBM / 1.75R`
  - OOS trades `4`
  - Win rate `50%`
  - Net-R payoff `1.23`
  - PF `1.23`
  - Expectancy `+0.14R`
  - LCB95 `-1.43R`
  - Positive windows `1/8`
  - 1.5x-cost expectancy `+0.02R`, stress PF `1.04`

对照当前 Testnet 基线（25 笔 closed、胜率 48%、Net-R payoff 约 0.577、PF 0.337、净期望为负），新 arm 只有 4 笔 OOS 且净期望为负，因此不能宣称优化成功。

最终状态：`CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED`。没有候选注册、晋级、Testnet 下单或执行平面改动。

## 验证命令

- `agent-python -m pytest -q tests/services/strategy_library/test_market_alpha.py` → `3 passed`
- `agent-python -m ruff check scripts/run_market_alpha_meta_research.py services/strategy_library/market_alpha.py tests/services/strategy_library/test_market_alpha.py` → `All checks passed!`
- `agent-python -m mypy scripts/run_market_alpha_meta_research.py services/strategy_library/market_alpha.py tests/services/strategy_library/test_market_alpha.py` → `Success: no issues found in 3 source files`
- `agent-python scripts/run_market_alpha_meta_research.py` → report status `CURRENT_MARKET_ALPHA_SOURCE_EXHAUSTED`, `holdout_accessed=false`, `accepted_arms=0`

## 证据

- `docs/audit/2026-08-14-market-alpha-meta-label.md`
- `artifacts/market_alpha/reports/market_alpha_meta_research.json`
- `artifacts/market_alpha/canonical/market_alpha_events.jsonl`
