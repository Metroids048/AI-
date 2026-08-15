# Edge-First Event 研究收口

日期：2026-08-14

## 目标

停止继续猜测 `xxx_v2/v3` 策略，改用 `Event -> Outcome -> Quality Gate`，并且不读取 sealed holdout。

## 实施

- 新增 `services/strategy_library/event_edge.py`：固定事件、首触 outcome、equal-risk 指标、嵌套训练/验证 gate。
- 新增 `scripts/run_edge_first_event_research.py`：8 个 12 个月训练 / 3 个月 OOS 窗口。
- 事件：`HTF_STRUCTURE_BREAK`、`HTF_BREAK_RETEST`。
- 首根 15m bar 纳入 barrier 判断；单币种持仓占用不允许事件重叠；指标按时间排序。
- 不创建 `TradeCandidate`，不改执行链、不改风控数值、不启用动态 sizing。

## 结果

- `artifacts/trading_audit/canonical/edge_events.jsonl`：1,258 条 development 事件。
- `artifacts/trading_audit/reports/edge_first_event_research.json`：`holdout_accessed=false`。
- 选中 OOS：23 笔，胜率 `43.48%`，Net-R payoff `1.178`，PF `0.9065`，Expectancy `-0.06098R`，LCB95 `-0.5826R`，正窗口 `0/8`。
- 最终：`STRATEGY_EDGE_NOT_FOUND`。

## 验证

- 新增测试：4 passed。
- 全仓 pytest：`1600 passed, 7 skipped, 7 warnings`。
- 受影响文件 ruff / mypy：通过。
- 全仓 ruff：被既有 `scripts/verify_gate17_e2e.py:77` 阻断。
- 全仓 mypy：被既有重复模块 `check_positions.py` 阻断。
