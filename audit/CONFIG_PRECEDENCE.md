# Config Precedence

已观察到的优先级：

1. 桌面启动器进程变量（Testnet、live 禁用、scheduler 模式）。
2. `shared.config.Settings` 的 `.env` 声明与代码默认。
3. `services/execution/bootstrap.py` 创建 PaperRun 的 execution profile。
4. `ConfigSnapshotRepository.activate_pending()` 在 `PaperCycleOrchestrator.run_cycle()` 开始时激活快照。
5. `paper_run.execution_profile` 与 `strategy.rules` 在 active snapshot 存在时被覆盖为 snapshot 内容。
6. 数据库 `paper_runs`、`trading_config_snapshots`、`decision_snapshots` 保存运行时结果。

账本中同一 PaperRun 有 3 个配置快照（migration baseline + 两个 testnet-acceptance），其 config hash 不同；`paper_runs.execution_config_hash` 仍为 null。该字段缺失使事件无法稳定关联配置身份。
