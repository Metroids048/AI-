# Runtime Observability 与 Market Neutral Research V1

## 任务边界

- 先收口 Loop A，再执行 Loop B Data Audit。
- Loop B 仅研究 H1/H2/H3；Runtime、Canary、Production Manifest、Promotion Gate、ConfigSnapshot 与 Binance execution 冻结。

## 已验证事实

- Loop A 24h 审计：221 个有效决策、2 个订单、1 个成交、1 个已平仓；当前 Runtime healthy，开放仓位 0/2，终态 `HEALTHY_WAITING_FOR_MARKET`。
- 策略过滤与操作阻断已分层；`MAX_OPEN_EXPOSURES` 已纳入入口阻断；`signal_generated` 改为基础信号 telemetry，不再由 `candidate_key` 推断。
- Loop B 固定五币种 Spot 1h 缓存仅覆盖 2023-11 至 2025-02，每个币种 16 个归档；未满足 2023-01-29 至 2026-01-29 全历史。
- 现有 `microstructure-v3` 只有 BTC/ETH futures daily metrics 与 monthly aggTrades，不是所需 Perpetual 1h、mark/index/premium、funding 历史或交易规则快照。

## 结果

- 生成 `artifacts/market_neutral_research_v1/` 全套 artifacts。
- Terminal：`BLOCKED_MARKET_NEUTRAL_DATA`。
- Research、Validation、Stability 未运行；Final Holdout 保持 sealed 且未访问。
- Runtime modified：false；Production：`NOT_GRANTED`。

## 验证记录

- `python scripts/audit_market_neutral_data.py` -> `BLOCKED_MARKET_NEUTRAL_DATA`。
- `git diff --check` -> 通过。
- Loop A targeted pytest：5 passed，随后因既有测试数据库缺少 `telegram_trade_threads` 表导致 fixture setup errors；该环境问题未修改。
- Full `pytest -q` after the telemetry contract update：`1904 passed, 7 skipped, 20 failed`；20 failures are existing async tests without `pytest-asyncio`。
