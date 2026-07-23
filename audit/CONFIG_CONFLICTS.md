# Config Conflicts

- 启动器固定 BTC/ETH，但研究/观察 lane 在 24 小时决策中出现 SOL；不能把观察数据误读成执行标的。
- 同名风险字段在 bootstrap 的多个策略模板出现不同默认（包括 `risk_per_trade`、`max_leverage`、`max_position_fraction`），而用户要求沿用当前固定配置；本轮不选择或修改它们。
- `RUNTIME_SCHEDULER_MODE` 的 API 默认、桌面环境变量、Celery compose 校验和独立 scheduler 并存，形成双入口/模式漂移风险。
- `execution_config_hash` 在 PaperRun 记录为空，而 ConfigSnapshot 有 hash；事件账本无法证明每个 order 使用哪一个快照。
- `BINANCE_USE_TESTNET`、`LIVE_TRADING_ENABLED` 的布尔值来自字符串环境变量；本次只确认启动脚本显式设置，未对所有部署环境做实盘试运行。
