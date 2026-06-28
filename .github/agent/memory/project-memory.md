# Project Memory

## Identity

- 项目名：AI Quant Research Platform
- 目标：实现研究报告定义的完整量化研究平台，而不是想法体检器或单纯回测脚本
- 主市场：第一阶段以 `BTC/USDT 永续` 为主
- 主语言：Python
- 管理后台：React + Tailwind

## Stable Constraints

- 报告是主架构真源
- 六层架构不可删层
- 风控系统必须是执行层核心，而不是后补
- Review Layer 必须每日回写策略库
- WorldQuant 只作为 E级研究数据与辅助来源

## Current Phase

- Phase 0：平台骨架与统一模型

## Active Design Sources

- 第一层真源：研究报告
- 第二层真源：`AGENTS.md`
- 第二层实施母文档：`docs/architecture/platform-master-design.md`
- 设计附录：
  - `appendix-a-repository-structure.md`
  - `appendix-b-feature-phasing.md`
  - `appendix-c-principles-and-non-goals.md`

## Planned Repository Structure

- `apps/api`
- `frontend/admin`
- `services/data`
- `services/strategy_library`
- `services/agents`
- `services/validation`
- `services/execution`
- `services/review`
- `research_source/worldquant_adapter`
- `tests`

## Primary Deliverables For This Phase

1. 全局项目配置
2. 项目记忆体系
3. 统一领域模型
4. 后端主干
5. 初始前端骨架
6. 平台总设计包母文档
