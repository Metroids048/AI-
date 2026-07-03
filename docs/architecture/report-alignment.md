# Report Alignment

本文件只保留“报告 -> 仓库层级映射”的速览版。完整真源层级、Phase 语义、文档职责边界见
[design-source-index.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\design-source-index.md)。

## Canonical Rule

- `AI_Quant_Research_Platform_完整报告.docx` 是最终真源。
- `AGENTS.md` 是仓库内执行真源。
- 其余设计文档只能展开，不得推翻上述两者。

## Layer Mapping

- `services/data` -> Data Layer
- `services/strategy_library` -> Strategy Layer
- `services/agents` -> AI Agent Layer
- `services/validation` -> Validation Layer
- `services/execution` -> Execution Layer
- `services/review` -> Review Layer
- `apps/api` -> 平台接口与编排入口
- `frontend/admin` -> 管理后台

## Current Status

- 当前仓库仍处于 `Phase 0`：平台骨架 + 统一模型 + 设计冻结
- `P0/P1/P2` 仅用于实现 tranche 标签，不等同于仓库整体 phase 状态
- 首个实现里程碑固定为“资金费率/基差套利策略跑通到模拟盘准入就绪”

## Entry Docs

- 真源入口：[design-source-index.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\design-source-index.md)
- 技术落地入口：`docs/architecture/technical-architecture-plan.md`
- 产品验收入口：`docs/product/prd.md`
