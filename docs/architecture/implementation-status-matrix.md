# Implementation Status Matrix

## 2026-07-05 P0 Hygiene Addendum

- 当前仓库状态统一表述为 `Phase 0 完成 + 第一批 P1 落地`，不再把已实现的 A 级数据、Paper/Risk/Review 首批切片误标为 Phase 2 以后。
- Compose 运行契约改为：`.env.example` 只作为模板，runtime `env_file` 必须使用 `.env`；CI 在 compose 校验前从模板复制临时 `.env`。
- 仓库卫生新增守卫：运行数据库、`.env` 与私钥类文件不得被 Git 跟踪；用户可见 Markdown 不得链接到本机 Windows 绝对路径。
- 下一轮 P1 顺序固定为：1. Celery Beat / 7x24 调度；2. 前端管理台补齐；3. B/C/D 级数据源接入。

## 2026-07-04 Tranche 1 Security/Ops Addendum

- `/api/v1/*` 现已通过 `apps/api/auth.py` 强制单租户 Bearer-token 鉴权；`/health` 与 `/api/v1/health` 保持公开。
- 通知能力已从 audit-only outbox 升级为真实 dispatch 闭环：`services/notifications.py` 提供 Telegram/Webhook adapter、重试/退避、attempt history 持久化，`/api/v1/notifications/outbox/dispatch` 与 `services.notifications_tasks.dispatch_notification_outbox` 共享同一派送逻辑。
- `frontend/admin` 的本地 build 已恢复为稳定校验项，`frontend/admin/src/main.jsx` 会发送 `VITE_ADMIN_API_TOKEN`（默认回退 `dev-admin-token`）以匹配新的管理面鉴权基线。
- `scripts/compose_validate.py` 已成为标准 compose 校验入口；本机因 `docker` 不在 PATH 仅能得到 documented skip，CI 使用 `--require-docker` 做强制校验。

## 2026-07-03 Remediation Plan Addendum

- Engineering baseline has been restored locally: `apps/__init__.py` defines the package boundary, Ruff B008 is scoped to FastAPI router files, and `apps/api/config.py` now requires the declared `pydantic-settings` dependency instead of carrying a local fallback.
- Validation Layer first slice now includes real walk-forward/OOS/stress diagnostics under `services/validation/{walk_forward,report,stress_scenarios}.py`.
- Public APIs added under the existing `/api/v1` prefix: `POST /api/v1/backtests/carry/walk-forward`, `GET /api/v1/validation/reports/{backtest_run_id}`, `GET /api/v1/system/health/dependencies`, `GET /api/v1/market/capabilities`, and persisted notification outbox APIs under `/api/v1/notifications/outbox`.
- `BacktestReport` now carries `validation_windows`, `stress_test_results`, and `lookahead_check`; `IngestionJob` carries `data_quality_summary`; `ExchangeCapability` and `NotificationOutboxItem` are exported from `shared/models`.
- `Makefile` no longer hides operational gaps behind `echo TODO`: `data-check` and `backtest` call real script entrypoints, while unsupported umbrella targets fail explicitly with guidance.
- Agent tasks with no registered executor now fail explicitly; deterministic Decision Veto and Review executor slices exist and do not generate orders.
- Binance public smoke is present as an opt-in integration test (`RUN_BINANCE_INTEGRATION=1`), and the default CI/test path remains offline deterministic.

## 2026-07-03 Binance Data Layer First-Tranche Addendum

- Binance public market-data ingestion is now implemented for the first Paper-console tranche.
- `DataRepository` writes to `ohlcv_bars` and `market_extras` are idempotent for repeated REST backfill / WS collector data.
- `services/data/binance.py` now provides CCXT-backed OHLCV/funding backfill services plus WS payload handlers that ignore in-progress Kline candles.
- `services/data/tasks.py` executes `binance_ohlcv_backfill` and `binance_funding_backfill` ingestion jobs and records `binance_live_market_collector` as a long-lived worker seam.
- `frontend/admin/vite.config.js` proxies `/api` to `http://127.0.0.1:8000` by default, with `VITE_API_BASE_URL` retained as an override.
- Scope remains Data Layer only: no live order execution, account sync, order-book persistence, alerting, LLM veto, or frontend push channel.

## 2026-07-03 Phase 1a/1b/1d/1e Verification Addendum

- `services/data/` has been restored in-repo and import smoke passes: `import services.data; import apps.api.main`.
- `.gitignore` now anchors root runtime data as `/data/`, `/artifacts/`, `/coverage/`, and ignores `/.pytest_ai_quant.db` plus `*.egg-info/`.
- LLM dependencies are optional under the `llm` extra; dev install no longer pulls `anthropic` / `langchain` / `llama-index`.
- Carry backtest metrics are no longer hardcoded. Sharpe, max drawdown, profit factor, expectancy, win rate, DSR-style penalty, and cost breakdown are calculated from trade returns / PnL.
- Carry fixtures that fail net expectancy after real fees/slippage/funding are now rejected instead of marked conditional.
- SignalEnsemble / MetaLabel service and API routes are implemented for the first deterministic service slice.
- Technical signals implemented: MACD and Dow swing trend. Chan theory remains not implemented by decision.
- WorldQuant alpha semantic evaluator is explicitly deferred per 2026-07-03 user instruction; keep only the existing scan/intake seam in active scope.
- Verification: `py -3 -m pip install -e ".[dev]"` passed; targeted Phase 1 tests passed (`14 passed`); full `py -3 -m pytest -q` passed (`31 passed`).
- Not locally verified: `docker compose -f docker-compose.yml config`, because `docker` is not available on this machine PATH.

## 2026-07-03 Paper Trading Console Addendum

- Added read APIs for the first Paper console: `/api/v1/market/snapshot`, `/api/v1/market/ohlcv`, and `/api/v1/console/overview`.
- Added small control APIs for Paper status and RiskEvent acknowledgement: `PATCH /api/v1/execution/paper-runs/{id}/status` and `PATCH /api/v1/risk/events/{id}/resolution`.
- `frontend/admin` is now a real Paper trading console that polls APIs, renders Binance Kline data with `lightweight-charts`, and displays carry, orders, positions, risk events, and manual controls.
- Scope remains Paper-first and Binance-first. Real exchange WebSocket ingestion, account sync, order placement/cancel, and live trading controls remain not implemented.
- Verification: `npm --workspace frontend/admin run build` passed; full `py -3 -m pytest -q` passed (`35 passed`); Playwright desktop/mobile smoke verified no mobile horizontal overflow after the chart resize fix.

更新时间：2026-07-03

本表用于把设计真源与仓库真实实现状态对账，避免继续被旧文档里的“空实现”描述误导。

状态说明：

- `implemented`：已有真实代码路径、持久化或测试覆盖
- `partial`：已有骨架或部分能力，但闭环未完整
- `missing`：尚未进入真实实现

| 模块 | 所属层 | 状态 | 真实落地证据 | 测试/验证 |
|---|---|---|---|---|
| 统一领域契约 `shared/models` | Cross-layer | implemented | `shared/models/{strategy,workflow,backtest,risk,signal,api}.py` | `tests/contracts/test_shared_models.py` |
| Strategy 生命周期持久化 | Strategy Layer | implemented | `services/strategy_library/{models,repository}.py` | `tests/repositories/test_strategy_repository.py` |
| FastAPI `/api/v1` 六类主接口 | API Layer | implemented | `apps/api/main.py` + `apps/api/routers/*.py` | `tests/api/test_health.py` |
| API 管理令牌鉴权 | API Layer | implemented | `apps/api/auth.py` + `ADMIN_API_TOKEN` + `apps/api/main.py` middleware；`/health` 与 `/api/v1/health` 保持公开 | `tests/api/test_health.py` |
| Binance A 级时序仓储 | Data Layer | implemented | `services/data/repository.py` (`ohlcv_bars`, `market_extras`, `risk_events`) | `tests/services/test_timeseries_repository.py` |
| Binance carry 回测应用服务 | Validation Layer | implemented | `services/validation/{carry,application}.py` | `tests/services/test_backtest_application.py`, `tests/api/test_vertical_slice.py` |
| 通用回测提交接口 | Validation Layer | partial | `POST /api/v1/backtests` 已改为提交请求并生成 `TaskSubmission`，但仍是同步落库 seam | `tests/api/test_vertical_slice.py` |
| 优化任务持久化 | Validation Layer | partial | `OptimizationRun` ORM + repository + `/api/v1/optimizations` | API 列表/提交已覆盖；walk-forward 已覆盖 carry lane，通用优化/DSR 引擎仍未完整落地 |
| Paper admission gate | Execution Layer | implemented | `services/execution/gatekeeper.py` | `tests/api/test_vertical_slice.py` |
| Order gatekeeper | Execution Layer | implemented | 无止损/数据不新鲜/validation fail/veto/blocking risk event 拒绝 | `tests/api/test_risk_review_agents.py` |
| RiskProfile 持久化 | Risk Layer | implemented | `RiskProfileRepository` + `/api/v1/risk/profiles` | `tests/api/test_risk_review_agents.py` |
| RiskEvent 持久化 | Risk Layer | implemented | `risk_events` timeseries table + `DataRepository.store_risk_event()` | `tests/api/test_risk_review_agents.py` |
| ReviewReport/FailureRecord 回写 | Review Layer | implemented | `services/review/service.py` + `ReviewRepository.create_failure()` | `tests/api/test_risk_review_agents.py` |
| AgentTask 状态机与结构化 I/O | Agent Layer | implemented | `AgentTaskRepository` + `services/agents/service.py` | `tests/api/test_risk_review_agents.py` |
| 本地 alpha 扫描 -> StrategyIdea | Research Source | implemented | `research_source/worldquant_adapter/{expression_parser,local_alpha_scanner}.py` | `tests/api/test_risk_review_agents.py` |
| WorldQuant 算子方法论移植 | Research Source | partial | `operators.py`、`CryptoFactorGenerator` 已有首版可执行实现 | 尚缺针对真实 crypto 因子输出的专项验证 |
| SignalEnsemble / MetaLabel 持久化 | Strategy Layer | partial | ORM + repository + `services/strategy_library/ensemble/service.py` + `/api/v1/strategy/ensemble/*` | `tests/api/test_signal_ensemble.py` 覆盖 deterministic service/API；训练型 meta-label 模型仍未落地 |
| LiveRun / PositionSnapshot 基础仓储 | Execution Layer | partial | ORM + repository + `/api/v1/execution/live-runs|positions` | 暂无 live 运行闭环测试 |
| Frontend Admin 控制台 | Frontend | partial | `frontend/admin` 已升级为 Paper Trading Console；K线、carry、订单、持仓、风险事件、人工操作面板接真实 API，并已接入 Bearer token 请求头 | `npm --workspace frontend/admin run build` 通过；Playwright 桌面/移动 smoke 通过 |
| Prometheus / Grafana dashboard | Ops | partial | `docker-compose.yml` 已新增 `prometheus`，Grafana dashboard provisioning 与 `research-loop-overview.json` 已入仓 | 本轮未跑 compose 验证 |
| `docker-compose.test/paper/live` overlays | Ops | partial | `docker-compose.{test,paper,live}.yml` 已新增，`scripts/compose_validate.py` 与 CI `compose-validate` 已脚本化校验入口 | 本机 `docker` 不在 PATH；仅完成 skip/CI 路径验证，未做本地 runtime smoke |
| News/Twitter/Telegram/Decision Veto Agent | Agent Layer | partial | schema 与 order gate veto 输入已接缝；`decision_veto_agent/pre_execution_veto` 有 deterministic executor | `tests/api/test_remediation_plan.py` 覆盖未注册 executor 不得标记完成；LLM 接入仍未实现 |
| Walk-forward / OOS / stress engine | Validation Layer | partial | `services/validation/{walk_forward,report,stress_scenarios}.py` + carry walk-forward API + report API | `tests/api/test_remediation_plan.py` 覆盖 OOS/压力结果影响 GateDecision；Deflated Sharpe 仍是字段/门槛口径，未实现完整统计校正引擎 |
| Exchange capability registry | Data/Execution boundary | partial | `services/data/capabilities.py` + `GET /api/v1/market/capabilities` | `tests/api/test_remediation_plan.py` |
| Notification outbox / dispatcher | Ops / Review / Risk | partial | `notification_outbox` ORM/migration + `services/notifications.py` + `GET/POST/PATCH /api/v1/notifications/outbox` + `POST /api/v1/notifications/outbox/dispatch`；高/critical `RiskEvent` 自动写入待处理通知，首批 Telegram/Webhook adapter 已可真实投递并回写 attempt history | `tests/api/test_remediation_plan.py`、`tests/services/test_notifications.py`；Email adapter 与真实运维凭据演练仍未实现 |
| System dependency health | Ops | partial | `apps/api/routers/system.py` + `GET /api/v1/system/health/dependencies` | `tests/api/test_remediation_plan.py`；当前为配置/连通性可见性，不替代外部监控 |
