# 风险门禁迭代审查证据

审查日期：2026-07-12
基线：当前 `main` 工作区，未启用 Mainnet

## 实测通过

| 项目 | 证据 | 结论 |
|---|---|---|
| Python 回归 | `agent-python -m pytest -q -m "not integration"` | 245 passed，1 deselected，2 warnings |
| Python 质量 | `agent-python -m ruff check .`、`agent-python -m mypy` | 迭代前均通过 |
| 前端 | Vitest 24 tests、Vite build、`npm audit --audit-level=high` | 均通过；构建仍提示 bundle >500kB |
| 策略冷启动 | MetaLabel 少于 20 个样本 | fail-closed，不下注 |
| 多周期 | `4h_direction_15m_entry` 缺失 4h 数据 | fail-closed，不降级放行 |
| Top20 scope | PaperOrchestrationService 默认准备 run | 固定运营 Top20，显式研究 scope 不受影响 |
| Testnet 预检 | `agent-python scripts/testnet_preflight.py` | Mainnet disabled；Futures credentials configured；账户连通；0 持仓 |
| Futures Testnet 验收 | `agent-python scripts/run_testnet_acceptance.py` | 20/20 币种、40 笔成交、0 持仓、0 挂单、无补偿 |
| 干净数据库启动 | `scripts/prepare_database.py` + Uvicorn `/health` | Alembic 0001..0006、本地时序表和 API lifespan 均通过；`/health` 200 |

## 仍未验证或被环境阻断

| 项目 | 当前状态 |
|---|---|
| Spot/Futures 双腿真实 Carry | Spot Testnet 凭据未配置，不能声称真实双账户闭环 |
| Docker Compose / PostgreSQL / Redis 运行态 | 本机 Docker 不在 PATH，未做容器级验证 |
| 2 小时真实 Celery soak | 尚未完成；本轮有调度器故障恢复单测和 Celery 配置门禁，但不替代长时运行证据 |
| pip-audit | Agent Python 环境未安装 `pip_audit` 模块 |
| 普通 Codex Security 扫描 | 代码修复与运行证据完成后执行；本报告不冒充已完成扫描 |
| 浏览器截图复验 | Browser 插件不可用且仓库未安装 Playwright；Vitest/build 通过但不替代截图证据 |

## 变更摘要

- MetaLabel 新增最小训练样本门槛，防止正收益冷启动误下注。
- 显式多周期确认缺失时 fail-closed；无配置时不隐式依赖其他周期。
- PaperRun 自动默认改为 `fixed_operator_top20`。
- Testnet 验收新增逐币阶段、订单引用、补偿和失败分类证据，并加入 120 USDT/币默认上限。
- Celery 启用 late ack、Worker 丢失重排、started 状态和结果保留；运行状态暴露任务成功/失败时间与 Top20 覆盖。
- 前端 Top20 显示候选、采集、信号、Gate、提交、成交六级证据；Ops 不再通过 GET 隐式刷新外部数据。
