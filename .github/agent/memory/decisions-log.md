# Decisions Log

## ADR-001: 研究报告作为主架构真源

- Date: 2026-06-28
- Status: accepted
- Context: 用户明确要求严格按研究报告实现整体架构与闭环。
- Decision: 将 `AI_Quant_Research_Platform_完整报告.docx` 作为主真源，并在 `AGENTS.md` 中固化为仓库级约束。
- Consequences: 后续实现不能擅自简化为单策略工具或以 WorldQuant 为主架构中心。

## ADR-002: 先做治理层，再做实现层

- Date: 2026-06-28
- Status: accepted
- Context: 当前工作区基本空白，直接写业务代码容易偏离报告。
- Decision: 先建立 `AGENTS.md`、项目记忆、项目元数据与基础配置，再继续仓库骨架和代码实现。
- Consequences: 后续每轮开发都以同一套记忆与约束为准，降低架构漂移。

## ADR-003: 第一阶段主市场为 BTC/USDT 永续，但模型支持多市场

- Date: 2026-06-28
- Status: accepted
- Context: 报告要求先从加密单品种深耕，但未来扩展到多资产。
- Decision: 领域模型从第一天起设计为多市场兼容，当前主流程围绕 `BTC/USDT perpetual`。
- Consequences: 不允许把市场、交易所、标的硬编码成不可扩展结构。

## ADR-004: 平台总设计包作为仓库内第二层实施母文档

- Date: 2026-06-28
- Status: accepted
- Context: 当前仓库已有治理层与骨架层，但缺少能直接指导后续子设计与实现的母文档。
- Decision: 新增 `docs/architecture/platform-master-design.md` 及 3 个附录，作为研究报告之下、子设计文档之上的总设计包。
- Consequences: 后续领域模型、接口、数据接入、Agent 编排、执行/风控/复盘设计都必须引用此母文档，不得各自重新定义平台边界。
