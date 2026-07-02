# LLM API 接入与辅助决策方案

## 文档定位

本文件是开发前文档包的第 5 份，回答"LLM 在平台里具体怎么被调用"这一方案设计问题。

按用户确认的范围边界：**本文件只做方案设计（Prompt 结构、调用时机、成本控制），
不写 anthropic SDK 的实际接入代码**。当前 `pyproject.toml` 已声明 `anthropic`/
`langchain`/`llama-index` 依赖，但仓库内零代码引用（见技术架构方案 §12 缺口表），
本文件是这些依赖被真正使用前必须先固定的设计基础。

本文件的裁决**不得**扩大 LLM 的决策权限——这是最重要的边界。AGENTS.md 明确
"AI 的职责是研究、规则化、编码、分析、复盘与知识沉淀，不是主观预测涨跌"；
ADR-013 已裁定 LLM 只承担"一票否决 + 复盘生成"，不做方向/仓位/价格决策。
本文件在这个已裁定的边界内做工程化展开，不重新讨论边界本身。

---

## 01 LLM 在六层架构中的职责清单（边界重申 + 展开）

### 1.1 允许 LLM 做的事（承接 11 个 Agent 的既有职责）

| Agent | LLM 用途 | 输出对象 | 是否可影响执行 |
|---|---|---|---|
| Strategy Agent | 把自然语言假设规则化为结构化草案 | `StrategyDraft` | 否——草案需人工审核才能晋升 |
| Coding Agent | 生成策略代码工件 | `StrategyCodeArtifact` | 否——代码需过编译检查+回测才进入执行链路 |
| Research Agent | 从外部研究源提炼 `StrategyIdea` | `StrategyIdea` | 否——只是研究入口候选 |
| News Agent | 对新闻内容做严重度分类 | `RiskEvent`（分类字段） | 是，但只影响风险等级标签，不直接下单（见 §1.2） |
| Twitter/Telegram Agent | 解析社媒信号/结构化 `TradeSignal` | `TradeSignal`/`RiskEvent` | 否——TradeSignal 仍需过 SignalEnsemble/MetaLabel/风控才可能被采纳 |
| Review Agent | 生成复盘日报文本、失效模式归因 | `ReviewReport` | 否——复盘结论驱动的是"建议"，晋升/淘汰仍是人工决策点 |
| **Decision Veto Agent** | 信号融合后、执行前的一票否决 | `veto: bool + veto_reason` | **是，但只能是二元否决，不能输出方向/仓位/价格**（ADR-013） |

### 1.2 明确禁止 LLM 做的事

- 不能直接输出交易方向、入场价、止损价、止盈价、仓位大小——这些字段的产生路径固定为
  规则化策略代码 -> 回测验证 -> 信号融合 -> MetaLabel 二级仓位判定，LLM 在这条链路上
  只能参与"规则化"和"融合后否决"两个节点，不能替代中间的量化计算节点。
- 不能把 News/Twitter/Telegram Agent 的分类结果直接映射为下单指令；这些 Agent 的输出
  只能进入 `RiskEvent`/研究入口，走既定的风控分级触发规则（execution-risk-review-design.md
  §2.2a），不能新开一条"LLM 认为该开仓/平仓"的旁路。
- 不能在没有 `SignalEnsemble`/`MetaLabel` 前置产出的情况下被调用做否决——Decision Veto
  Agent 的输入是明确的（`SignalEnsemble`/`MetaLabel`/近期 `RiskEvent`），不能省略前置步骤
  直接对着原始市场数据做"感觉判断"。
- 不能修改已生成的信号内容（如"把方向从 long 改成 short"），只能否决/不否决整个信号。

---

## 02 Prompt 结构设计原则

### 2.1 结构化输入优先，禁止裸拼接自然语言上下文

每一类 Agent 调用都必须遵循"结构化对象 -> 序列化 -> Prompt 模板"的路径，不允许把多个来源的
原始文本直接拼接后丢给模型（这也是 AGENTS.md "所有 Agent 必须通过结构化对象和任务通信，
不得用'随手写文件'串联流程"在 LLM 调用层面的落地）。

统一 Prompt 模板结构（四段式，各 Agent 复用同一骨架，只替换职责段和数据段）：

```
[SYSTEM] 角色与边界声明段
  - 声明该 Agent 的职责边界（引用本文件 §1.1/§1.2 对应行）
  - 声明输出必须是结构化 JSON，字段与目标 Pydantic 模型一致
  - 声明禁止事项（如 Decision Veto Agent 的 Prompt 必须显式声明"不得输出方向/价格/仓位"）

[CONTEXT] 结构化数据段
  - 直接序列化相关 shared.models 对象（如 SignalEnsemble/MetaLabel/近期 RiskEvent 列表）
  - 不包含未经清洗的原始网页/社媒文本全文，只包含已被上游 Agent/数据管道提炼过的字段

[TASK] 任务指令段
  - 明确的单一任务描述（一次调用只做一件事，不设计"顺便也分析一下"的多任务 Prompt）

[OUTPUT SCHEMA] 输出结构约束段
  - 显式给出期望的 JSON 结构（字段名对齐目标 Pydantic 模型），要求模型仅输出该结构
```

### 2.2 各 Agent 的 Prompt 差异点（职责段示例，非最终文案）

| Agent | 职责段核心声明 | 输出结构对齐对象 |
|---|---|---|
| Strategy Agent | "把交易假设转成结构化规则草案，不评价假设是否会赚钱" | `StrategyDraft`（entry/exit/stoploss/takeprofit/position rules） |
| Coding Agent | "把已审核的规则草案转成可执行代码，不改变规则语义" | `StrategyCodeArtifact`（含 `generation_notes` 记录任何必要的实现取舍） |
| News Agent | "只做严重度分类和影响范围判断，不判断该不该开仓" | `RiskEvent`（`severity`/`affected_scope`） |
| Review Agent | "基于已产出的运行数据做归因和建议，不重新计算指标（指标已由回测/执行系统算好）" | `ReviewReport`（`deviation_analysis`/`recommendations`） |
| Decision Veto Agent | "只输出二元否决和理由，不输出任何方向/价格/仓位；不确定时倾向不否决，把判断留给既定风控规则" | `veto: bool + veto_reason` |

Decision Veto Agent 的"不确定时倾向不否决"是一条关键设计原则：LLM 的否决权是在已有的
规则化风控体系之上的**额外一层保守过滤**，不是替代品；如果 LLM 因为模型幻觉而过度否决，
后果是错过交易机会（可接受、可复盘），但如果反过来设计成"不确定时倾向否决对方向的怀疑"，
容易演变成 LLM 隐性地在做方向判断，违反边界。

### 2.3 输出结构化校验

所有 LLM 输出必须经过 Pydantic 模型校验后才能写入 `AgentTask.output_ref`；校验失败
（字段缺失/类型不匹配/输出了越界内容如价格数字出现在 Decision Veto Agent 的输出里）
必须使该次 `AgentTask` 标记为 `failed`，不允许"尽量解析出能用的部分"这种宽松容错，
因为宽松容错在 LLM 场景下容易掩盖模型输出了违反边界内容的事实。

---

## 03 调用时机设计

### 3.1 各 Agent 的触发时机（对齐 agent-and-orchestration-design.md §04 主链路）

| Agent | 触发时机 | 触发方式 |
|---|---|---|
| Strategy Agent | `StrategyIdea` 被人工提交或 Research Agent 产出后 | 同步（API 请求触发）或异步（Celery 任务） |
| Coding Agent | `StrategyDraft` 通过人工审核（`approved`）后 | 异步（Celery 任务，`agent_queue`） |
| Research Agent | 外部研究源 `IngestionJob` 完成后（定时/手动触发） | 异步（Celery Beat 定时或手动触发） |
| News Agent | C 级新闻 `IngestionJob` 产出新记录后 | 异步（数据管道触发，`agent_queue`） |
| Review Agent | 每日固定时点（复盘日报） + 策略状态变化事件 | 异步（Celery Beat 定时） |
| **Decision Veto Agent** | `MetaLabel.bet_decision = bet_taken` 之后、`ExecutionSignal` 生成之前 | **同步阻塞**——执行链路必须等待否决结果才能继续，不能异步后台跑 |

Decision Veto Agent 是唯一要求同步阻塞调用的场景，因为它处于执行前置检查的关键路径上
（execution-risk-review-design.md §1.3）。其余 Agent 调用均可异步，不阻塞主链路。

### 3.2 同步阻塞调用的超时与降级

由于 LLM API 调用延迟不可控（网络/限流/模型排队），Decision Veto Agent 的同步调用必须有
明确的超时与降级策略，否则会拖慢执行链路甚至造成信号过期：

- 设定调用超时阈值（具体秒数留给 Phase 1 实现按实测延迟校准，本文件只定义"必须有超时"）。
- 超时后的降级行为需要产品/风控层面二选一确认（本文件列出两个选项，具体选择留给
  风控措施与保障方案文档裁决，因为这是一个风险容忍度问题而非纯技术问题）：
  - 选项 A："超时即否决"（保守，可能错过机会，但不会绕过否决层直接执行）
  - 选项 B："超时视为无否决意见，继续走既定风控规则"（更接近"LLM 否决是锦上添花层"的定位）
- 无论选择哪个选项，超时事件本身必须记录为 `AgentTask` 失败，供复盘追溯，不能静默降级。

---

## 04 成本控制方案

### 04.1 成本的主要来源与控制杠杆

| 成本来源 | 主要驱动因素 | 控制杠杆 |
|---|---|---|
| Decision Veto Agent 高频调用 | 每次进入执行前置检查都要调用一次，模拟盘/实盘信号频率越高成本越高 | 只对已通过 SignalEnsemble/MetaLabel 且 `bet_decision=bet_taken` 的信号调用，不对所有候选信号调用（该约束已在 §1.2/§3.1 隐含，本处强调其成本收益） |
| News/Twitter/Telegram Agent 持续分类 | C/D 级数据源接入后每条新记录都可能触发一次调用 | 分级预处理：先用轻量规则/关键词过滤明显无关内容，只把可能相关的内容送入 LLM 分类（不要求 P0 实现具体过滤规则，只要求这一预处理层必须存在） |
| Review Agent 每日复盘生成 | 频率固定（每日一次+状态变化事件），相对可控 | 批量化：同一日的多策略复盘尽量合并为较少次数的调用，而不是每个策略单独调用一次 |
| Coding/Strategy Agent | 频率与研究节奏相关，通常远低于交易执行频率 | 天然较低，不需要特别控制 |

### 4.2 模型分级调用策略

不同 Agent 对模型能力的要求不同，不需要统一用最高能力模型：

- **需要强推理能力**：Strategy Agent（自然语言到规则的转换质量直接影响下游）、
  Review Agent（归因分析的质量影响复盘价值）、Decision Veto Agent（否决判断的可靠性）。
- **可用较低成本模型**：News/Twitter/Telegram Agent 的初步分类任务（严重度分类本质是
  一个多分类问题，不需要顶级模型的深度推理能力）。

具体模型型号选择是 Phase 1 实现细节（且模型本身会迭代），本文件只定义"按任务复杂度分级选择
模型，不是所有调用都使用同一固定型号"这一原则，`.env.example` 中 `CLAUDE_MODEL` 变量
应扩展为按 Agent 类型可配置（如 `CLAUDE_MODEL_VETO`/`CLAUDE_MODEL_NEWS_CLASSIFY` 等，
或提供一个 Agent 类型到模型型号的配置映射，而非单一全局变量），具体命名留给实现阶段。

### 4.3 缓存与重复调用抑制

- Research Agent 对同一批外部研究源内容不应重复调用分析（若内容未变化，复用上次
  `AgentTask.output_ref`）。
- News Agent 对同一事件的重复报道（多个 RSS 源报道同一新闻）应先去重（可用标题/内容相似度
  做轻量去重），避免同一事件被多次分类消耗多次调用。
- Decision Veto Agent 不适用缓存（每次否决判断都对应一个具体的、时效性极强的信号，
  不能复用历史否决结果）。

### 4.4 成本可观测性

- 每次 LLM 调用必须记录：Agent 类型、模型型号、输入/输出 token 数（若 API 返回该信息）、
  调用耗时、调用结果（成功/失败/超时）——这些字段可作为 `AgentTask` 的扩展字段或关联的
  独立调用日志对象，具体归属留给 Phase 1 领域模型细化。
- 建议按 Agent 类型、按日/周聚合成本报表，作为 Review Layer 的运维侧输出之一
  （不是策略复盘的内容，而是平台运维复盘的内容，两者不要混在同一份 `ReviewReport` 里）。
- 成本异常（某类调用短时间内暴增）应能触发告警（依赖技术架构方案 §09 登记的通知层，
  P1 才具备）。

---

## 05 可靠性与降级设计

### 5.1 LLM 服务不可用时的分级降级

| Agent | 服务不可用时的降级行为 |
|---|---|
| Strategy/Coding/Research Agent | 任务标记为 `failed`，人工可后续重试，不阻塞其他链路（这些 Agent 不在同步执行路径上） |
| News/Twitter/Telegram Agent | 任务标记为 `failed`，对应的数据记录保留原始内容，等待重试；不能因为分类失败就丢弃原始数据 |
| Review Agent | 若无法生成完整复盘文本，仍应产出"数据摘要 + 生成失败标注"的降级版报告，而不是完全不产出报告（当天没有复盘视图，比研究者习惯的每日节奏断裂的风险更大） |
| **Decision Veto Agent** | 见 §3.2 的超时降级策略；LLM 服务整体不可用等价于持续超时，走同一降级路径 |

### 5.2 不允许的降级方式

- 不允许"LLM 不可用时自动放宽风控阈值来补偿"——LLM 层的故障绝不能转化为风控层的放松，
  这与"风控优先级永远高于收益"的不可谈判项直接冲突。
- 不允许"LLM 输出解析失败时启用一个简化的本地规则引擎自动顶替"而不留痕——如果确实需要
  本地规则兜底（例如 Decision Veto Agent 的超时兜底选择"不否决"），必须清晰标注这是
  兜底行为而非真实的模型判断，避免复盘时误判为"LLM 认为可以执行"。

---

## 06 安全与合规边界

- API Key（`CLAUDE_API_KEY`）只通过环境变量注入，不写入代码/日志/Prompt 内容。
- Prompt 中不包含交易所 API Key、数据库连接串等敏感凭据——LLM 调用的输入数据严格限定为
  `shared.models` 中定义的业务对象序列化结果，不应把 `Settings` 对象整体传给模型。
- LLM 输出（尤其是 Review Agent 生成的复盘文本、Decision Veto Agent 的否决理由）在展示给
  研究者时必须标注"AI 生成"（呼应 `prd.md` §4.6 验收标准），不能让输出内容看起来像人工分析。
- 涉及 D 级社媒原始内容（可能包含他人发言）送入 LLM 分类时，只做严重度/相关性分类用途，
  不用于生成可能涉及具体人物的对外可见内容，避免衍生出与本平台"内部研究台"定位不符的
  内容生成用途。

---

## 07 与 LangChain / LlamaIndex 的角色边界

- `LangChain`/`LlamaIndex` 的既定用途是"RAG 与知识检索层"（AGENTS.md 必需技术栈），
  服务于 Research Agent 对 E 级研究数据（论文/GitHub/WorldQuant 本地库）的检索增强，
  **不用于**执行链路上的任何 Agent（尤其不用于 Decision Veto Agent，其输入必须是
  明确的结构化对象，不应引入检索增强带来的不确定性来源）。
- 知识检索层的索引内容范围：`FailureRecord`（失败知识库检索，供新策略设计时参考历史教训）、
  E 级研究源的本地缓存内容。范围内不包含实时市场数据、执行记录（这些不是"知识"，
  是"事实数据"，应直接查询 PostgreSQL/TimescaleDB，不需要向量检索）。
- 该检索层的具体实现（索引更新频率、向量库选型）是 Phase 1/2 的实现细节，本文件只定义
  用途边界。

---

## 08 P0/P1 实现边界

- P0：本方案文档产出，`.env.example` 的 `CLAUDE_API_KEY`/`CLAUDE_MODEL` 骨架已存在
  （已确认现状），不要求任何实际调用代码。
- P1：Strategy/Coding Agent 的 LLM 调用实现（对应 appendix-b 排序中"技术策略框架化"
  之后的 Agent 在线化阶段）、Decision Veto Agent 实现（依赖 SignalEnsemble/MetaLabel
  已有实际数据产出）、成本记录字段落地。
- P2：News/Twitter/Telegram Agent 的 LLM 分类实现（依赖 C/D 级数据源真实接入，
  本身是 P1 排序中较后的项）、知识检索层（RAG）实现。

---

## 09 下一步

本文件完成后，下一份交付是 24 小时自动实时交易运行方案。
