# 2026-08-16 PF口径对齐与Funding归因

## 结论

- `1.1123`、`0.50915`、`0.3966` 分别对应 replay、同 cohort exchange-fill、32 episode account audit，不能混用。
- `0.48688` 在当前仓库/产物中不可追溯，当前 30 笔 normalized-R PF 为 `0.75334826`，旧值暂标 unsupported。
- funding 仅能做时间窗命中，不能做唯一策略归因：3 个 event / 30 笔、朴素窗口和 `+1.33323099 USDT`，且快照上下文 stale/外部持仓冲突。停止 funding-only 实验，状态 `INSUFFICIENT_DATA`。
- P2-B 不是 0 条记录；三候选写在 `v2_execution_decisions.payload.research_shadow`。cutover 后 unique 5,607 条：trend 1,869、range 1,869、failed-breakout 1,869；后者有 16 条 `SHADOW_SIGNAL_READY`、5 条 `SHADOW_STRATEGY_REJECTED`。

## 证据

- [PF cohort alignment](/C:/Users/Windows11/Desktop/量化项目/docs/audits/2026-08-16-pf-cohort-alignment.md)
- [Funding attribution](/C:/Users/Windows11/Desktop/量化项目/docs/audits/2026-08-16-p2a-funding-attribution.md)
- [P2-B embedded shadow](/C:/Users/Windows11/Desktop/量化项目/docs/audits/2026-08-16-p2b-embedded-shadow.md)

## 范围

本轮为只读审计与证据落盘；未改 entry/exit 几何、执行链、Gatekeeper、风控阈值、production authorization，也未强平/撤单/下单。
