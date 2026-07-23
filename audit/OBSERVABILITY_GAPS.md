# Observability Gaps

## Event coverage

要求的 21 个事件中，当前 `decision_events` 表记录数为 **0**；`decision_snapshots` 有 1839 行，但不是逐阶段事件账本。可在 snapshot trace 中看到 pipeline_status、strategy_lane、signals、ensemble、meta_label、veto_result 等键，但没有稳定的 `run_id, cycle_id, stage, decision, reason_code, config_hash, market_timestamp, input_count, output_count, payload_hash, exception_type` 全字段。

因此以下事件只能从 snapshot/order 推断，不能视为已观测：`candidate.created`、所有 accepted/rejected 阶段事件、`trade_intent.created`、`risk.*`、`order.submitted/acknowledged/rejected`、`position.reconciled`、`exit_intent.created`。

## 数量守恒

24 小时 snapshot 可统计主 lane 58、observation lane 137；主 lane 17 个 gateway failure；observation lane 82 个 ensemble discard；LLM veto 0。由于没有逐阶段 input/accepted/rejected/error 事件，无法证明任何阶段满足 `input = accepted + rejected + explicit_error`。报告明确记为 `UNOBSERVABLE`，不以零填补。

## 时间线污染

一个 scheduler cycle 从 `2026-07-22 09:59:50` 运行至 `2026-07-23 01:29:59`；代码在 lease 丢失时不会 fence/cancel shielded runner，但当前账本没有 lease-loss 字段，不能证明这次长 cycle 实际经历了 lease loss。ETH 交易所成交在 `2026-07-23 09:29:28.611`，本地 decision snapshot 却把订单提及挂到前一 cycle。这阻断了“卖点基于哪个 candle/保护价”的确定性回答。
