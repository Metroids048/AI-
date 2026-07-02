# Implementation Status Matrix

更新时间：2026-07-02

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
| Binance A 级时序仓储 | Data Layer | implemented | `services/data/repository.py` (`ohlcv_bars`, `market_extras`, `risk_events`) | `tests/services/test_timeseries_repository.py` |
| Binance carry 回测应用服务 | Validation Layer | implemented | `services/validation/{carry,application}.py` | `tests/services/test_backtest_application.py`, `tests/api/test_vertical_slice.py` |
| 通用回测提交接口 | Validation Layer | partial | `POST /api/v1/backtests` 已改为提交请求并生成 `TaskSubmission`，但仍是同步落库 seam | `tests/api/test_vertical_slice.py` |
| 优化任务持久化 | Validation Layer | partial | `OptimizationRun` ORM + repository + `/api/v1/optimizations` | API 列表/提交已覆盖，尚无 walk-forward/DSR 引擎测试 |
| Paper admission gate | Execution Layer | implemented | `services/execution/gatekeeper.py` | `tests/api/test_vertical_slice.py` |
| Order gatekeeper | Execution Layer | implemented | 无止损/数据不新鲜/validation fail/veto/blocking risk event 拒绝 | `tests/api/test_risk_review_agents.py` |
| RiskProfile 持久化 | Risk Layer | implemented | `RiskProfileRepository` + `/api/v1/risk/profiles` | `tests/api/test_risk_review_agents.py` |
| RiskEvent 持久化 | Risk Layer | implemented | `risk_events` timeseries table + `DataRepository.store_risk_event()` | `tests/api/test_risk_review_agents.py` |
| ReviewReport/FailureRecord 回写 | Review Layer | implemented | `services/review/service.py` + `ReviewRepository.create_failure()` | `tests/api/test_risk_review_agents.py` |
| AgentTask 状态机与结构化 I/O | Agent Layer | implemented | `AgentTaskRepository` + `services/agents/service.py` | `tests/api/test_risk_review_agents.py` |
| 本地 alpha 扫描 -> StrategyIdea | Research Source | implemented | `research_source/worldquant_adapter/{expression_parser,local_alpha_scanner}.py` | `tests/api/test_risk_review_agents.py` |
| WorldQuant 算子方法论移植 | Research Source | partial | `operators.py`、`CryptoFactorGenerator` 已有首版可执行实现 | 尚缺针对真实 crypto 因子输出的专项验证 |
| SignalEnsemble / MetaLabel 持久化 | Strategy Layer | partial | ORM + repository 已存在 | 尚无服务层与 API 闭环 |
| LiveRun / PositionSnapshot 基础仓储 | Execution Layer | partial | ORM + repository + `/api/v1/execution/live-runs|positions` | 暂无 live 运行闭环测试 |
| Frontend Admin 控制台 | Frontend | partial | `frontend/admin` 已从占位页升级为 React + Tailwind 控制台壳 | `npm run build` 通过 |
| Prometheus / Grafana dashboard | Ops | partial | `docker-compose.yml` 已新增 `prometheus`，Grafana dashboard provisioning 与 `research-loop-overview.json` 已入仓 | 本轮未跑 compose 验证 |
| `docker-compose.test/paper/live` overlays | Ops | partial | `docker-compose.{test,paper,live}.yml` 已新增 | 本轮未跑 compose 验证 |
| News/Twitter/Telegram/Decision Veto Agent | Agent Layer | partial | schema 与 order gate veto 输入已接缝，Agent 真实执行未接 LLM | 无 |
| Walk-forward / OOS / Deflated Sharpe / stress engine | Validation Layer | missing | 仅在模型与文档中定义，尚未成为真实服务 | 无 |
