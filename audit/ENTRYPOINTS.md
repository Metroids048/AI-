# Entrypoints

## 桌面 Paper 入口（已确认）

`一键启动.cmd` -> `scripts/launch-paper-console.ps1`。脚本设置 `BINANCE_USE_TESTNET=true`、`LIVE_TRADING_ENABLED=false`、`PAPER_CONSOLE_API_ONLY=true`，启动 `apps.api.local_server`（默认 8016）和 `scripts/run-local-paper-scheduler.py`。API 只读/控制台请求与 scheduler 进程分离。

## 其他入口

- `apps/api/main.py`：当 `RUNTIME_SCHEDULER_MODE=inprocess` 且 autostart 时创建 `RuntimeScheduler`；桌面 API-only 模式跳过。
- `apps/api/celery_app.py`：Beat 将 `services.execution.tasks.run_all_paper_runtime_cycles` 投递到 `paper_queue`。
- `apps/api/routers/runs.py`：`/paper-runs/{id}/auto-cycle`、`/paper-runs/auto-cycle-all`、`/manual-orders`、`/close-position`、`/orders`、`/live-runs/{id}/orders`。
- `scripts/start-paper-engine.ps1`、Docker compose、system/PM2 未发现另一条当前桌面入口；历史脚本均标记 archive。

## 运行模式与安全

本次未调用任何 POST 下单、撤单、清仓、Testnet acceptance 或 simulation acceptance。代码审计确认 gateway 下单方法存在，但本轮只读查询未触发。
