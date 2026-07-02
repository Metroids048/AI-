# 设计真源索引与对账总表

## 文档定位

本文件是开发入口索引，不新增架构决策。职责只有四个：

1. 固定真源层级，避免“哪份文档说了算”继续漂移。
2. 重置 `Phase 0` 与 `P0/P1/P2` 的语义边界，避免把“设计阶段”误读成“功能已落地”。
3. 给出每份核心文档负责裁决什么、不负责裁决什么。
4. 明确非正式材料（如 `策略库/笔记.docx`）在研究闭环中的角色。

---

## 01 真源层级

1. **主真源**：`AI_Quant_Research_Platform_完整报告.docx`
2. **仓库级执行真源**：`AGENTS.md`
3. **平台母文档**：
   - `docs/architecture/platform-master-design.md`
   - `docs/architecture/domain-and-interfaces-design.md`
   - `docs/architecture/data-and-ingestion-design.md`
   - `docs/architecture/agent-and-orchestration-design.md`
   - `docs/architecture/execution-risk-review-design.md`
4. **第二轮专项方案**：
   - `docs/architecture/technical-architecture-plan.md`
   - `docs/architecture/validation-methodology.md`
   - `docs/architecture/llm-integration-plan.md`
   - `docs/architecture/24x7-operations-plan.md`
   - `docs/architecture/external-data-source-integration-plan.md`
   - `docs/architecture/risk-control-and-safeguards-plan.md`
   - `docs/architecture/strategy-library-collection-and-scoring.md`
5. **产品细化文档**：
   - 上层定位：`docs/product/product-spec.md`、`docs/product/feature-catalog.md`
   - 开发验收真源：`docs/product/prd.md`、`docs/product/module-feature-catalog.md`
6. **索引/记忆/入口文档**：
   - `README.md`
   - `docs/architecture/report-alignment.md`
   - `.github/agent/memory/*.md`

规则：

- 下层文档只能展开上层，不得静默推翻上层。
- 若专项方案与主报告冲突，以主报告与 `AGENTS.md` 为准；专项方案负责把留白裁决清楚，不负责改写平台定位。
- `README.md`、记忆文件、对账索引属于“入口层”，它们负责指路与同步现状，不是新的真源。

---

## 02 Phase 语义

- **当前仓库状态仍是 `Phase 0`**：平台骨架、统一模型、设计冻结阶段。
- `Phase 0` 不代表“P0 功能已真实可运行”，只代表骨架、契约、文档与入口口径已收束。
- `appendix-b-feature-phasing.md`、`module-feature-catalog.md` 中的 `P0/P1/P2` 是**实现 tranche 标签**：
  - `P0`：在 `Phase 0` 期间完成框架与最小能力定义，不等同于所有能力已上线。
  - `P1`：第一条可执行主线，固定为 `BTC/USDT` 永续 -> 资金费率/基差套利 -> 历史回测 -> 样本外 -> 模拟盘准入。
  - `P2`：在首条主线稳定后，逐步扩展更多数据源、更多策略和更完整的运行能力。
- **首个开发里程碑止于“模拟盘准入就绪”**，不把 live 实盘作为第一实现 tranche。

---

## 03 文档职责边界

| 文档 | 负责裁决 | 不负责裁决 |
|---|---|---|
| `platform-master-design.md` | 六层架构、产品边界、人工决策点 | 实现级 API/队列/配置细节 |
| `domain-and-interfaces-design.md` | 领域对象、状态流、接口簇边界 | 具体阈值、具体供应商、具体算法参数 |
| `technical-architecture-plan.md` | 目录、服务、队列、配置、已知技术缺口 | 产品验收标准、风控数值裁决 |
| `validation-methodology.md` | walk-forward、Deflated Sharpe、压力测试、成本建模边界 | 具体公式代码实现 |
| `llm-integration-plan.md` | LLM 使用边界、Prompt 结构、降级原则 | 交易方向/仓位决策 |
| `risk-control-and-safeguards-plan.md` | 风控阈值、Key 权限自检、熔断规则留白裁决 | 回测算法、产品旅程 |
| `product-spec.md` / `feature-catalog.md` | 上层定位、总功能类目 | 字段级接口、验收细节 |
| `prd.md` / `module-feature-catalog.md` | 开发验收口径、模块级行为与字段承接 | 六层重定义、部署拓扑 |

---

## 04 非正式材料边界

- `策略库/笔记.docx` 是**研究素材池**，不是正式策略真源。
- 它只能通过 `StrategyIdea -> StrategyDraft -> StrategyContract` 流程进入主链路。
- 其中内容应拆成三类：
  - 可规则化假设
  - 待验证指标/观察项
  - 需要淘汰的主观描述
- WorldQuant、本地 A 股经验、新闻/社媒、技术指标笔记都视为“来源”，不视为可直接执行策略。

---

## 05 当前代码对账入口

- 契约真源：`shared/models/`
- API 骨架：`apps/api/routers/`
- 持久化现状：`migrations/` 仅有 `strategies` 表，其他生命周期对象仍未落库
- 运行骨架：`docker-compose*.yml`、`apps/api/celery_app.py`
- 研究来源接缝：`research_source/worldquant_adapter/`

若需要判断“这项能力是已经有代码、还是只有文档”，先看：

1. 本文件的真源层级
2. `technical-architecture-plan.md` §12 已知技术缺口
3. `tests/` 是否已有对应契约/API smoke
