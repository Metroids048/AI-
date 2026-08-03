# 03 实施验证报告：V2 Active 修复验收

日期：2026-08-03
施工依据：[02-frozen-solution-v1.0.md](02-frozen-solution-v1.0.md)；运行时落点校准：[02-frozen-solution-v1.1-runtime-remap.md](02-frozen-solution-v1.1-runtime-remap.md)
诊断参考：[01-project-diagnostic-report.md](01-project-diagnostic-report.md)

## 结论

当前实际 writer 为 **V2 Active**，已针对该实际链路完成原 S-101 至 S-203 的最小映射与修复；未修改 legacy writer 代码。

- 页面/API 已保存的 `PaperRun.execution_profile` 通过既有 `ConfigSnapshot` 在 V2 下一周期成为运行时真源。
- bootstrap 重启不再覆盖操作员的杠杆、仓位、风险 tier 和 sampling 开关。
- sampling 默认关闭；即使显式开启，也只保留 V2 决策事实，不创建 intent、不提交交易所订单、不投影本地仓位。
- V2 新开仓先使用 Binance 的独立 `set_leverage()`，失败时不调用 `create_order()`；ReduceOnly 出口路径未改。
- V2 漂移门槛已是 `20 bps / 0.25 ATR`，以回归测试固定，未复制 legacy 的 `100 bps / 0.40 ATR` 放宽。

本轮未发出交易所请求、未写入运行数据库、未执行迁移、未修改前端/依赖/DEGRADED 语义、MACD/EMA/ADX 或主策略买卖点。

## 阶段 0 与当前调用链

阶段 0 的只读证据仍为：`logs/scheduler.log` 记录 `V2 Engine Activation: ACTIVE | ... | Allow Legacy: False`；运行库最新 `v2_execution_intents` / `v2_exchange_orders` 为 `2026-07-31 01:15:36`，带 exchange order；最后可识别 legacy `paper_signal_generator` 订单更早。

已核实的实际链路：

```text
apps/api/main.py
  -> bootstrap_local_paper_runtime()
  -> bootstrap_auto_trading_technical_paper_run()
  -> RuntimeScheduler
  -> execute_v2_automated_trading_cycles()
  -> CycleRequest
  -> run_automated_trading_cycle()
  -> execute_entry()
  -> BinanceTestnetAdapter.submit_market_order()
```

引擎状态由 `resolve_engine_activation(settings)` 决定；`ACTIVE + allow_legacy_writer=False` 只注册 V2 `automated_trading_v2_cycle`，不注册 legacy writer。标准启动前的 bootstrap 是实际配置初始化点，不是 legacy writer，因此仅按 S-101/S-201 修复其 profile 默认/合并逻辑。

## S-ID 实施结果

| 方案 | 问题 | 实施与证据 | 状态 |
| --- | --- | --- | --- |
| S-101 | P1-003、P1-004 | 新 directional run 默认 `simulation_sampling_fallback_enabled=False`；bootstrap 保留显式 False；V2 `CycleRequest` 默认关闭 | 完成 |
| S-102 | P1-005 | V2 adapter 在 entry `create_order()` 前调用 `set_leverage()`；失败抛出明确 `leverage configuration failed`，不提交订单 | 完成 |
| S-103 | P1-006 | V2 原有实现已为 `20 bps / 0.25 ATR`；新增 80 bps 严格拒绝、模拟 100 bps 才会通过的回归测试 | 完成（复用现有正确实现） |
| S-104 | P1-003 | V2 无 `reference_price * 0.0015` 路径；回归测试固定 BTC/ETH 同风险预算的名义金额相同 | 完成（不存在待删除实现） |
| S-105 | P1-004 | `non_promotable` sampling candidate 在 V2 只写决策 trace 并立即返回；不会创建 intent、调用 entry、交易所下单或投影仓位。V2 没有 sampling-driven `opposite_signal` 分支；现有 forced risk/time/reduce-only exit 保持 | 完成 |
| S-201 | P1-001 | bootstrap 增加完整 operator 字段保留集；V2 scheduler 激活既有 `NEXT_CYCLE` snapshot 后读取 profile，不创建第二配置表 | 完成 |
| S-202 | P1-002 | 新 V2 profile resolver：tier leverage → profile max leverage → runtime default；profile `order_notional_usdt` → risk budget；同时执行 tier/profile exposure 上限 | 完成 |
| S-203 | P1-001、P1-002 | 集成测试构造保存后的 `NEXT_CYCLE` snapshot，断言下一 V2 request 获得 `risk=0.012`、`leverage=7`、`notional=123`、`exposure=0.11`、`sampling=false` | 完成 |

## 实际修改文件

- `services/execution/bootstrap.py`：实际启动初始化的 operator profile 保留与 sampling 安全默认值。
- `services/execution/v2_scheduler_entry.py`：读取现有 directional profile、激活 NEXT_CYCLE、构造 V2 请求。
- `services/automated_trading/application/operator_profile.py`：V2 对既有 profile 的单一 precedence resolver。
- `services/automated_trading/application/cycle_service.py`：profile sizing、sampling default-off 和 decision-trace-only 边界。
- `services/automated_trading/infrastructure/binance_adapter.py`：entry leverage fail-closed。
- 对应 V2/bootstrap 单元与集成测试文件。

## 验证记录

| 命令 | 结果 |
| --- | --- |
| `pre-commit install` | 通过；本地 hook 已安装 |
| 定向 V2 回归（cycle、entry、adapter、scheduler、contract） | `120 passed` |
| API、Testnet contract、natural cycle contract | `57 passed, 3 skipped`；跳过项为既有环境条件 |
| 扩展相关回归（含 bootstrap/API/V2） | `203 passed, 3 skipped` |
| `ruff check .` | `All checks passed!` |
| `mypy` | `Success: no issues found in 219 source files` |
| `pytest -q -m "not integration"`（AGENT_PYTHON） | 未完成：收集阶段缺少既有可选依赖 `joblib`，未改依赖 |
| `py -3 -m pytest -q -m "not integration"`（仓库解析出的项目 runner） | 未完成：120 秒上限内无汇总输出而超时；未重复运行 |

所有新增行为均先以失败测试确认旧行为，再进行最小代码修改并跑定向回归；S-103/S-104 在 V2 中已无错误实现，因此以明确回归测试锁定现状，而未引入无关改动。

## 验收边界

代码、单元、集成和 API 合同验收已满足。本机当前没有运行中的 API/调度器，且本轮未获授权重启运行时或发起新的 Binance Testnet 订单；因此没有新增的自然交易所成交证据。该外部验收未被视为通过，也不影响上述无外部副作用的回归结论。
