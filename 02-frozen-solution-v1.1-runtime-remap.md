# 冻结方案 v1.1：V2 Active 运行时落点映射

## 范围与依据

本文件仅校准 `02-frozen-solution-v1.0.md` 的代码落点；问题编号、根因、目标行为、验收标准和禁止范围均不变。

阶段 0 的只读证据确认最近实际写单者为 V2 Active：`logs/scheduler.log` 记录 `V2 Engine Activation: ACTIVE` 且 `Allow Legacy: False`；运行库中最新 `v2_execution_intents` / `v2_exchange_orders` 记录含交易所订单，而可识别的 legacy `paper_signal_generator` 订单更早。当前代码的写单调用链为：

`RuntimeScheduler -> services.execution.v2_scheduler_entry.execute_v2_automated_trading_cycles -> CycleRequest -> services.automated_trading.application.cycle_service.run_automated_trading_cycle -> entry_service.execute_entry -> BinanceTestnetAdapter.submit_market_order`。

`resolve_engine_activation(settings)` 负责解析引擎状态；在 `ACTIVE` 下 `resolve_scheduler_v2_jobs()` 注册 `automated_trading_v2_cycle`，并且仅在 `allow_legacy_writer=True` 时注册 legacy 周期。阶段 0 的运行日志为 `Allow Legacy: False`，因此 legacy 不在本次核心写单链路内。

## 最小映射

| 原问题编号 | 原方案编号 | 原 legacy 落点 | 当前 V2 Active 落点 | 映射证据 | 是否可继续实施 |
|---|---|---|---|---|---|
| P1-003、P1-004 | S-101 | `bootstrap.py` 的 `simulation_sampling_fallback_enabled` 默认/保留逻辑 | `bootstrap.py::_ensure_auto_paper_run`（V2 启动前初始化）、`v2_scheduler_entry.py` 构造 `CycleRequest` 前的运行配置读取、`cycle_service.py` 在生成 sampling decision 前的开关 | `apps/api/main.py` 在标准启动调用 `bootstrap_local_paper_runtime()`，后者调用 technical directional bootstrap；随后 V2 scheduler 读取该 run。`cycle_service.py` 固定构造 `CandidateLane.TESTNET_SAMPLING` | 是 |
| P1-005 | S-102 | `gateway.py` 吞掉 `set_leverage()` 异常 | `services/automated_trading/infrastructure/binance_adapter.py::submit_market_order` | 该方法把 `leverage` 作为 `create_order` 参数传入，未先显式设置杠杆，也没有失败关闭路径；它是 `entry_service.execute_entry` 的唯一 V2 提交点 | 是 |
| P1-006 | S-103 | `shared/config.py` 的放宽常量 | `decision_service.py::DEFAULT_MAX_ENTRY_DRIFT_BPS` 与 `entry_service.py::drift_ceiling_bps` | V2 当前已为 `20 bps` 和 `0.25 * ATR`；须以回归测试锁定，不复制或放宽 legacy 常量 | 是（现有实现复用） |
| P1-003 | S-104 | `paper_signal.py` 的 `reference_price * 0.0015` | `cycle_service.py::_calculate_quantity` 与 `CycleRequest` 的名义金额输入 | V2 没有 price×0.0015；当前只以 `equity * risk_per_trade` 计算名义金额，需让操作员固定 `order_notional_usdt` 具有更高优先级 | 是 |
| P1-004 | S-105 | `paper_cycle_orchestrator.py` 的 sampling 开仓/`opposite_signal` | `cycle_service.py::run_automated_trading_cycle` 的 sampling candidate 后续 entry/intents/position 投影；V2 exit loop | V2 固定 lane 为 `TESTNET_SAMPLING`，其候选为 `non_promotable=True`，但仍进入 `persist_entry_intent_before_submission`、`execute_entry`、仓位与保护投影；exit loop 不根据本周期 sampling direction 触发 opposite-signal，因此保留现有正式仓位退出路径并禁止 sampling entry 即可闭环 | 是 |
| P1-001 | S-201 | `bootstrap.py` 合并覆盖已保存 profile | `bootstrap.py::_ensure_auto_paper_run` 的启动时 profile 合并，以及 V2 scheduler 对 `PaperRun.execution_profile` / `ConfigSnapshot` 的读取与 NEXT_CYCLE 激活 | 标准启动实际执行 bootstrap；原合并 `{**previous, **execution_profile, **preserved}` 会覆盖 risk/leverage/notional/tier 等。修复为 bootstrap 只刷新结构字段、保留已有 operator 字段；V2 不再忽略保存后的 profile | 是 |
| P1-002 | S-202 | `paper_signal.py::_requested_leverage/_requested_notional` | V2 profile resolver、`CycleRequest`、`_calculate_quantity` | scheduler 把 `risk_per_trade` 与 `max_leverage` 硬编码为 `PAPER_RUNTIME_LIMITS`；`_calculate_quantity` 无 `order_notional_usdt` 优先级 | 是 |
| P1-001、P1-002 | S-203 | legacy API → snapshot → bootstrap → order 合同 | `runs.py` 现有 auto-settings API → 已有 `ConfigSnapshot` → V2 scheduler → `CycleRequest` | `PATCH /paper-runs/{id}/auto-settings` 已写 profile 并创建 `NEXT_CYCLE` snapshot；V2 scheduler 尚未消费该合同。新增 V2 集成测试覆盖保存后的下一周期读取，且不需要数据库迁移或新配置表 | 是 |

## 明确未映射的旧落点

- 不修改 `paper_signal.py`、`paper_cycle_orchestrator.py`、`gateway.py`。`bootstrap.py` 例外：它是 V2 标准启动前实际调用的配置初始化点，而非 legacy writer，因此仅按 S-101/S-201 修改其 profile 默认/合并逻辑。
- 不修改 V2 的 MACD、EMA、ADX 或主策略买卖点。Sampling 仅保留决策事实；本轮不改变其信号计算。
- 不引入第二 writer、数据库迁移、前端或依赖变更。
