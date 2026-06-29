# Report Alignment

本文件记录仓库实现与研究报告的映射关系。

## Canonical Rule

- `AGENTS.md` 是仓库内的架构执行入口。
- `AI_Quant_Research_Platform_完整报告.docx` 是最终真源。

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

- Phase 0 in progress
- Governance and project memory initialized
- Repository skeleton and root config initialized
- Platform master design package added as the second-layer implementation source
- Domain and interfaces design package added as the next-level implementation spec

## Design Sources

- Canonical report: `AI_Quant_Research_Platform_完整报告.docx`
- Repository execution source: `AGENTS.md`
- Platform master design: `docs/architecture/platform-master-design.md`
- Domain and interfaces design: `docs/architecture/domain-and-interfaces-design.md`
- Appendices:
  - `docs/architecture/appendix-a-repository-structure.md`
  - `docs/architecture/appendix-b-feature-phasing.md`
  - `docs/architecture/appendix-c-principles-and-non-goals.md`
