# AI Quant Research Platform

本仓库用于实现研究报告定义的 `AI Quant Research Platform`。

当前状态：

- 已建立项目治理与记忆体系
- 已将研究报告约束固化进 [AGENTS.md](C:\Users\Windows11\Desktop\量化项目\AGENTS.md)
- 已完成平台前期准备包与开发前设计收敛索引
- 后续所有代码实现将严格按六层架构推进

当前语义：

- 仓库整体仍处于 `Phase 0`：平台骨架 + 统一模型 + 设计冻结
- `appendix-b-feature-phasing.md` 中的 `P0/P1/P2` 是实现 tranche 标签，不等同于仓库 phase
- 第一条真实开发主线固定为：`BTC/USDT` 永续 -> 资金费率/基差套利 -> 历史回测 -> 样本外 -> 模拟盘准入

设计入口：

- [设计真源索引与对账总表](C:\Users\Windows11\Desktop\量化项目\docs\architecture\design-source-index.md)

主真源：

- [AI_Quant_Research_Platform_完整报告.docx](C:\Users\Windows11\Desktop\量化项目\AI_Quant_Research_Platform_完整报告.docx)
- [AGENTS.md](C:\Users\Windows11\Desktop\量化项目\AGENTS.md)

项目记忆：

- [project-memory.md](C:\Users\Windows11\Desktop\量化项目\.github\agent\memory\project-memory.md)
- [decisions-log.md](C:\Users\Windows11\Desktop\量化项目\.github\agent\memory\decisions-log.md)
- [task-history.md](C:\Users\Windows11\Desktop\量化项目\.github\agent\memory\task-history.md)

第一阶段目标：

- 建立统一仓库骨架
- 定义领域模型、任务对象、风险对象
- 搭建 FastAPI API 主干
- 为 Validation / Execution / Review 预留清晰边界
- 不把 live 实盘作为第一实现里程碑

已完成的设计包：

- [设计真源索引与对账总表](C:\Users\Windows11\Desktop\量化项目\docs\architecture\design-source-index.md)
- [平台总设计包](C:\Users\Windows11\Desktop\量化项目\docs\architecture\platform-master-design.md)
- [领域与接口设计包](C:\Users\Windows11\Desktop\量化项目\docs\architecture\domain-and-interfaces-design.md)
- [数据与接入设计包](C:\Users\Windows11\Desktop\量化项目\docs\architecture\data-and-ingestion-design.md)
- [Agent 与任务编排设计包](C:\Users\Windows11\Desktop\量化项目\docs\architecture\agent-and-orchestration-design.md)
- [执行 / 风控 / 复盘设计包](C:\Users\Windows11\Desktop\量化项目\docs\architecture\execution-risk-review-design.md)
- [产品规格（上层定位）](C:\Users\Windows11\Desktop\量化项目\docs\product\product-spec.md)
- [功能清单总表（上层总表）](C:\Users\Windows11\Desktop\量化项目\docs\product\feature-catalog.md)
- [产品需求文档（开发验收）](C:\Users\Windows11\Desktop\量化项目\docs\product\prd.md)
- [模块功能清单（字段级承接）](C:\Users\Windows11\Desktop\量化项目\docs\product\module-feature-catalog.md)
- [阶段路线图](C:\Users\Windows11\Desktop\量化项目\docs\roadmap\phase-roadmap.md)
- [环境与配置规范](C:\Users\Windows11\Desktop\量化项目\docs\ops\environment-and-config.md)
- [前期准备交付清单](C:\Users\Windows11\Desktop\量化项目\docs\ops\delivery-checklist.md)

研究输入边界：

- [策略库/笔记.docx](<C:\Users\Windows11\Desktop\量化项目\策略库\笔记.docx>) 只作为研究素材池，不作为正式策略真源
- 任何来源都必须先进入 `StrategyIdea -> StrategyDraft -> StrategyContract`，再进入回测与验证
