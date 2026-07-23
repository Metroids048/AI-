# Config Map

只记录变量名、来源和覆盖关系，不记录值、密钥或完整 `.env`。

| 配置 | 启动器/环境 | Settings/默认 | PaperRun/快照 | 结论 |
|---|---|---|---|---|
| `BINANCE_USE_TESTNET` | `launch-paper-console.ps1` 强制 `true`；`.env.example` 声明 | `shared/config.py:96` 默认 true | execution profile 记录 execution mode | Mainnet 本轮禁用 |
| `LIVE_TRADING_ENABLED` | 启动器强制 `false` | `shared/config.py:100` 默认 false | 由 profile/guard 再检查 | 未发现本轮实盘调用 |
| `RUNTIME_SCHEDULER_MODE` | 桌面脚本设 `inprocess`，但独立 scheduler 另起进程 | Settings 默认 `inprocess` | 不写入 secret | 双入口风险 |
| `RUNTIME_SCHEDULER_AUTOSTART` | 桌面脚本设 true | Settings 默认值 | API-only 会跳过 | API 与外部 scheduler 语义不同 |
| `universe_assets` | bootstrap 与 PaperRun execution profile | `AUTO_PAPER_EXECUTION_SYMBOLS=(BTC/USDT, ETH/USDT)`；其他研究/观察 lane 可含 SOL | snapshot keys 可见 | directional execution 应锁 BTC/ETH；观察 lane 仍出现 SOL |
| `risk_per_trade`、`max_leverage`、`max_position_fraction`、`max_total_exposure` | bootstrap 多套 profile | `services/execution/bootstrap.py` 存在多组模板默认 | snapshot 只记录 key/hash | 固定配置来源不唯一，禁止动态化 |
| `strategy_rules` | bootstrap、ConfigSnapshot | StrategyRules 默认/模板 | 三个快照覆盖同一 run | 需以有效 snapshot 为准，时间线存在复用污染 |
