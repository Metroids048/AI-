# Silent Drop Inventory

本轮只读搜索按阶段分类。以下是影响交易链路的高信号项；完整逐行表见 `GATE_INVENTORY.csv`。

| 阶段 | 位置 | 条件/语义 | 日志或 reason | 风险 |
|---|---|---|---|---|
| scheduler | `services/execution/scheduler.py:394-405` | lease renewal 失败只写 `scheduler leadership lease was lost`；`run_task` 被 shield，旧 cycle 仍继续 | 有 scheduler error，无 cycle fencing 事件 | 长 cycle 继续写旧 slot/旧时间线 |
| decision | `services/execution/decision_pipeline.py` | base/MTF/ensemble/meta-label/LLM/gatekeeper 多层 reject | snapshot reason 部分存在 | 事件表为空，无法做输入守恒 |
| ensemble | `services/strategy_library/ensemble/service.py` | 低置信度返回 discarded | `discarded_low_confidence` | 聚焦测试确认多数票案例仍被丢弃 |
| execution | `services/execution/gateway.py:340-341` | TradeIntent 缺 market rules 直接抛 ValueError | 上层保存 `binance_auto_execute_failed` | 主 lane 17 次全部断在下单前 |
| reconciliation | `services/execution/paper_exchange_execution.py:327+` | 外部仓位按 symbol 恢复 | `reconcile_exchange_open_*` | 未校验来源/方向/identity |
| exit | `paper_cycle_orchestrator.py:1452+` | 最近 filled entry 只按 run/symbol | stoploss / local close | 可继承错误保护价 |
| optional dependency | `services/strategy_library/technical/pandas_ta_adapter.py:625` | `ta is None` 抛 ImportError | 有异常但环境层失败 | 8 个测试失败，非本轮回归 |

未发现把异常统一吞成 `return None` 的单一总开关；但多个 `except Exception`、`return None` 和缺少事件记录的上层路径仍会让周期显示成功。

CSV scope: GATE_INVENTORY.csv contains 811 rows: curated high-signal entries plus AST scans of non-archive Python under services/ and apps/. AST rows cover gate-like `if`, `return None/False`, skip/reject returns, `continue`, `pass`, and broad exception handlers. Fields that cannot be proven statically remain `unknown`; inventory rows are not proof of runtime behavior.
