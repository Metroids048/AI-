# Fade / Reversion 严格 OOS 研究收口

## 范围

- [事实] 未修改 `services/execution/**`、Binance adapter、Gatekeeper、V2 cycle/exit/reconciliation、风险数值或 active manifest 授权。
- [事实] Final Holdout 起点固定为 `2026-01-29T00:00:00+00:00`，所有本轮报告 `holdout_accessed=false`。
- [事实] Forward Baseline 与执行链不在本轮范围；既有 `FORWARD_REPRODUCIBLE_BASELINE_READY` 证据保持不变。

## 步骤 1：真实亏损归因

- [事实] 只读复核报告：`artifacts/strategy_refactor/reports/2026-08-15-live-loss-structure-attribution.json`。
- [事实] 当前本地只读库回放出 30 个 closed `testnet_sampling_v2` entries、14 个 STOP；这与用户摘要中的 25/12 不同，未将两者混写。
- [事实] Donchian-24 逐笔结构表：`artifacts/strategy_refactor/reports/2026-08-15-loss-structure-table.json`。
- [事实] 14 个 STOP 中，0 个同时满足“入场反向扫单 + 下一根确认”的严格假突破反手结构。
- [推断] 当前证据不支持把这批 STOP 归因成“本应反手却顺势入场”；止损几何问题仍可由真实亏损画像支持，但结构反手假设未被这批 entry 直接验证。

## 步骤 2：严格 EventEdge 评测

- [事实] `failed_breakout_reversal_v1`：3,592 个事件，8 窗口均无训练 gate 通过，结论 `REJECTED_WITH_EVIDENCE`。报告：`artifacts/strategy_refactor/reports/2026-08-15-failed-breakout-reversal-edge.json`。
- [事实] `range_sweep_reversion_v1`：严格按现有双边边界触碰与 range regime 条件得到 0 个事件，结论 `INSUFFICIENT_DATA`。报告：`artifacts/strategy_refactor/reports/2026-08-15-range-sweep-reversion-edge.json`。
- [事实] EventEdge 单目标映射仅用于研究：failed-breakout 使用 2R runner，range-sweep 使用 opposite boundary；没有把候选的多腿目标当成已可执行能力。
- [事实] 门槛未放宽：`min_trades=30`、`PF>=1.40`、`expectancy>=0.10R`、`expectancy_lcb95>0`。

## 步骤 3：独立消融

- [事实] RegimeScorerV2 权重消融报告：`artifacts/strategy_refactor/reports/2026-08-15-regime-weight-ablation.json`。比较了 `0.50/0.30/0.20`、当前代码 `0.20/0.35/0.45`、请求的 `0.20/0.30/0.50`，没有产生可晋级 gate；未并入候选主结论。
- [事实] Bollinger 消融报告：`artifacts/strategy_refactor/reports/2026-08-15-bollinger-ablation.json`。当前冻结 V2 `evaluate_sampling_signal` 只计算 EMA50、MACD、RSI14、ATR14，没有 Bollinger 分支；因此不存在可诚实比较的 V2 OOS delta。旧 `services/execution/decision_pipeline.py` 的 Bollinger 分支不被本轮改动。

## 唯一结论

- [事实] `failed_breakout_reversal_v1` = `REJECTED_WITH_EVIDENCE`。
- [事实] `range_sweep_reversion_v1` = `INSUFFICIENT_DATA`。
- [事实] 没有候选获得 `ACCEPTED_FOR_SHADOW_PROMOTION`；active manifest、production authorization、执行链均未改变。
