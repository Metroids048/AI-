# 2026-07-12 当前仓库真实状态复核

> 复核截面：2026-07-12；代码基线：`main@80ce3d6`，含用户现有未提交改动。  
> 原则：以当前源码和本轮门禁输出为准；项目记忆只作为历史线索，不把“曾经通过”当作当前证据。

## 1. 结论摘要

当前仓库不是早期脚手架，也不是简化的荐股工具。六层目录与 `AGENTS.md` 的 Data / Strategy / AI Agent / Validation / Execution / Review 架构一致，已经形成两条受同一套验证、风控和复盘边界约束的交易通道：资金费率 carry，以及技术方向性策略。方向性通道的真实执行链为“八类技术信号 -> 多周期确认 -> SignalEnsemble -> MetaLabel -> LLM 二元否决 -> Gatekeeper -> Binance-first 执行”。

本轮未发现会导致交易行为无法唯一解释、数据账本必然分叉或绕过 Validation/Gatekeeper 的核心阻塞。发现的主要问题属于文档与配置作用域不一致、历史格式漂移，以及尚未实现的后续能力；这些应进入优化路线图，但不阻止策略库 Playbook 迭代。

### 本轮新鲜门禁结果

| 门禁 | 结果 | 判断 |
|---|---:|---|
| 后端 `pytest -q` | `253 passed, 1 skipped, 2 warnings` | 通过；warning 不等于失败，需继续保留原始输出追踪 |
| 前端 Vitest | `26 passed` | 通过 |
| `ruff check .` | 通过 | lint 基线通过 |
| `mypy` | 通过 | 类型门禁通过 |
| 前端生产构建 | 通过 | 构建门禁通过 |
| `ruff format --check .` | 失败，65 个文件存在格式漂移 | 非核心阻塞；禁止借本任务批量格式化，后续只检查本轮触碰文件 |

CI 与本地门禁方向一致：`.github/workflows/ci.yml:22-32` 执行 Ruff lint、全仓 format、mypy 和非 integration pytest，`.github/workflows/ci.yml:37-43` 执行前端测试、high 级 npm audit 和生产构建。当前唯一明确的全仓工程门禁缺口是 65 个历史文件的 Ruff format 漂移。

## 2. 已完成能力与真实证据

### 2.1 六层闭环和 Binance-first

- TASK-039 已定位并修复 15m/4h 数据未维护导致自动方向性策略无法下单的问题，且以真实 Paper open 作为证据：`.github/agent/memory/task-history.md:52-58`。当前启动代码仍会为 `1m/15m/4h` 播种数据：`services/execution/bootstrap.py:387-411`。
- TASK-040~041 的对抗审计发现并修复 API 生命周期、日志泄密、幂等、最小名义金额和 fail-closed 等问题：`.github/agent/memory/task-history.md:36-50`。
- TASK-042 将固定 Top20、Binance Mock/Testnet simulation-first、对账和运行态可见性落到真实代码和 UI：`.github/agent/memory/task-history.md:27-34`。
- TASK-043 已实现分层风险、Futures Testnet 验收、Spot/Futures 双腿 carry 和交易工作台：`.github/agent/memory/task-history.md:18-25`。该条历史记录当时明确未声称外部 Testnet 验收完成；随后 TASK-045 的项目记忆记录了 20/20、40 fills 且最终零持仓零挂单的新证据：`.github/agent/memory/project-memory.md:3-10`。
- 自动执行仍保持 Binance-first：持仓存在时，保护性平仓会先过 Gatekeeper，再在启用交易所执行时调用 gateway，gateway 未接受则不继续本地平仓，见 `services/execution/paper_runtime.py:220-264`；反向信号平仓同样先走 gateway，见 `services/execution/paper_runtime.py:343-382`。这避免了本地账本先成交、交易所失败的状态分叉。

### 2.2 八类技术信号

`DecisionPipeline` 的默认白名单完整列出八类信号：`services/execution/decision_pipeline.py:55-66`；实际分派逐项调用对应生成器：`services/execution/decision_pipeline.py:266-294`。每类信号均有独立实现，而非固定结果桩：

| 信号 | 实现证据 | 接入证据 |
|---|---|---|
| MACD | `services/strategy_library/technical/macd.py:14` `generate_macd_signal` | `services/execution/decision_pipeline.py:276-277` |
| Dow trend | `services/strategy_library/technical/dow_trend.py:46` `generate_dow_trend_signal` | `services/execution/decision_pipeline.py:278-279` |
| Price action（含假突破） | `services/strategy_library/technical/price_action.py:10-152`，其中假突破函数在 `:107` | `services/execution/decision_pipeline.py:280-283` |
| RSI | `services/strategy_library/technical/indicators.py:10` | `services/execution/decision_pipeline.py:284-285` |
| EMA trend | `services/strategy_library/technical/indicators.py:54` | `services/execution/decision_pipeline.py:286-287` |
| ADX | `services/strategy_library/technical/indicators.py:85` | `services/execution/decision_pipeline.py:288-289` |
| VWAP | `services/strategy_library/technical/indicators.py:133` | `services/execution/decision_pipeline.py:290-291` |
| Bollinger | `services/strategy_library/technical/indicators.py:165` | `services/execution/decision_pipeline.py:292-293` |

没有任何技术信号时会 fail closed 为 `technical_signals_insufficient`，不会用最后两根 K 线伪造 fallback：`services/execution/decision_pipeline.py:137-149`。

### 2.3 多周期确认

- 策略若显式配置 `direction_timeframe` 与 `entry_timeframe`，入场周期匹配时会切换到方向周期；兼容的 `4h_direction_15m_entry` 模型也明确映射 15m -> 4h：`services/execution/decision_pipeline.py:524-533`。
- 确认周期重新读取最多 240 根 K 线并运行同一套技术信号；缺确认数据、缺信号或任一方向不可判定时 `confirmation_unavailable_fail_closed`，方向不同则 `disagreed`：`services/execution/decision_pipeline.py:296-338`。
- 自动 technical 通道当前是已验证的 1h 模板，不是 4h/15m：`services/execution/bootstrap.py:40-60`。4h/15m operator experience 策略明确标记 `default_enabled_for_auto_trading=False`：`services/execution/bootstrap.py:69-81`。因此“系统支持 4h/15m”与“当前自动默认是 1h 模板”应同时陈述，不能混写。
- 当前确认周期与入场周期使用同一 `enabled_signals` 集合（`services/execution/decision_pipeline.py:315-319`），尚未实现“4h 只看趋势、15m 只看假突破”的分工，这是优化项而非未接入多周期。

### 2.4 SignalEnsemble 相关性过滤与加权投票

- `SignalEnsembleService.create_ensemble` 先执行相关性过滤，再按 `weight * confidence` 分别汇总 long/short，输出胜方方向和置信度：`services/strategy_library/ensemble/service.py:27-80`。
- `_correlation_filter` 对达到相关阈值的信号对，选择 validation score 较弱的一方并执行 `weight *= 0.25`：`services/strategy_library/ensemble/service.py:120-139`。这里是“降为原权重的 25%”，不是减去 0.25。
- 信号候选使用最近最多 80 根收盘价构造方向化收益序列：`services/execution/decision_pipeline.py:548-561`；基础权重与 Market Intelligence 最高 0.30 的边界位于 `services/execution/decision_pipeline.py:564-578`。

### 2.5 MetaLabel 样本与阈值作用域

- 样本严格排除当前未闭合/信号 K 线：`closed_history = bars[:-1]`，并以两个 47 长切片配对生成最多 47 个相邻收益样本：`services/execution/decision_pipeline.py:581-594`。
- MetaLabel 拒绝 signal time 及之后的训练样本；下注条件为样本数达到最小值、胜率达到阈值、平均收益高于阈值：`services/strategy_library/ensemble/service.py:82-118`。当前项目记忆还记录了最少 20 个历史样本的 cold-start fail-closed：`.github/agent/memory/project-memory.md:3-6`。
- 实际调用优先读取策略 `entry_rules.meta_label_min_win_rate`，缺省时才回退 `0.45`：`services/execution/decision_pipeline.py:208-218`。

必须区分以下三个阈值作用域：

| 值 | 作用域 | 证据 | 当前判断 |
|---:|---|---|---|
| `0.48` | 人类可读策略说明 | `策略库/00_当前系统策略与开平单逻辑.md:34-38` | 文档值，不能代表当前自动策略 |
| `0.50` | 当前 `AUTO_PAPER_TECHNICAL_RULES` 显式配置 | `services/execution/bootstrap.py:40-56` | 当前自动技术模板实际采用的值 |
| `0.45` | 任意策略未配置该字段时的代码回退 | `services/execution/decision_pipeline.py:208-214` | 兼容性 fallback，不是自动模板默认 |

三者不一致是文档/配置治理问题，但不会使当前自动 technical 行为含糊：显式 `0.50` 会覆盖 `0.45` 回退。应在 Playbook 中按 `value + scope + source_ref` 展示，并将 `0.48` 文档修订为有明确作用域的说明。

### 2.6 LLM Decision Veto

- Prompt 明确禁止建议方向、价格和仓位，只允许 JSON 的 `veto: boolean` 与 `veto_reason: string`：`services/agents/llm_runtime.py:283-307`。
- 服务层对两个字段执行严格类型校验；超时和 schema/其他失败都写入 `safe_veto_applied=True` 并强制 `veto=True`：`services/agents/service.py:319-379`。
- DecisionPipeline 在 repository 不可用、日预算超限和非法 payload 时同样 fail closed：`services/execution/decision_pipeline.py:340-399`、`:436-448`；最终方向仍来自 ensemble，LLM 只能决定 `should_trade`，见 `services/execution/decision_pipeline.py:240-260`。
- RAG 当前不是向量检索。`services/agents/rag_context.py:19-42` 按关键词重合计分取前若干条，数据源是 `策略库/*.md` 与开源研究资产（`:87-125`）。对当前“只做二元否决”的用途可用，但扩展研究问答前应升级并评估召回质量。

### 2.7 Gatekeeper 纯规则关口

- Kill Switch、开仓必须有止损、LLM veto、必须存在且通过 Validation 的回测均在入单时校验：`services/execution/gatekeeper.py:102-123`。
- 风险配置缺失、风险状态缺失、数据不新鲜、阻断性风险事件均会拒绝：`services/execution/gatekeeper.py:125-151`。
- 单品种/总敞口、持仓数、杠杆、日周亏损、回撤、连续亏损、Martingale 与 API failure 的数值风控在 `services/execution/gatekeeper.py:184-228`。close-only 可跳过开仓数值风险，但仍保留审计语义，避免风控阻止减仓。
- 默认 `RiskProfile.max_leverage=3.0` 是通用领域模型值：`shared/models/risk.py:21-38`；Top20 medium preset 是 `10.0`：`shared/models/risk.py:41-59`。它们与策略自己的 position rules 是不同层级，不应称为同一个“平台默认杠杆”。

### 2.8 平仓优先级与只收紧移动止损

- Paper runtime 对已有仓位先解析保护价、应用 trailing ratchet、检查保护触发，触发后立即走 close-only 流程并 `continue`，所以保护性退出优先于反向信号：`services/execution/paper_runtime.py:203-320`。
- 同一 K 线同时触发时，long 和 short 都先检查 stop 再检查 take，因此保守地优先止损：`services/execution/paper_runtime.py:763-779`。
- trailing 仅在达到 `trail_after_r * initial_distance` 后，把 long stop 取 `max(old, entry)`、short stop 取 `min(old, entry)`；不够收紧则直接返回旧值：`services/execution/paper_runtime.py:715-760`。
- 反向信号只生成 `close_reason="opposite_signal"` 的平仓请求，成功后从 active positions 移除并 `continue`，不会在同一轮直接反手：`services/execution/paper_runtime.py:343-427`。

### 2.9 风险预算仓位公式与杠杆作用域

真实仓位公式位于 `services/execution/paper_signal.py:313-350`：

```text
risk_budget = account_equity * risk_per_trade
quantity = risk_budget / abs(reference_price - stoploss_price)
volatility_sized_notional = quantity * reference_price
requested_notional = min(volatility_sized_notional,
                         account_equity * max_position_fraction)
                     * confidence_multiplier
```

如果缺少有效止损距离才回退到 `risk_budget * max(requested_leverage, 1)`；因此“权益 × 1% / 止损距离”是正常主路径，不是固定手数。资产 risk tier 存在时，杠杆和最大仓位比例来自 tier：`services/execution/paper_signal.py:303-310`、`:340-347`。

杠杆默认同样必须按作用域陈述：

- operator 4h/15m 研究候选：`risk_per_trade=0.01`、`max_leverage=5`、`max_position_fraction=0.05`，证据 `services/execution/bootstrap.py:69-81`；该策略默认不自动交易。
- 当前自动 1h technical 模板：`0.01 / 10 / 0.15`，证据 `services/execution/bootstrap.py:40-66`，实际资产级 tier 还会覆盖统一值。
- 通用 `RiskProfile` Pydantic 默认：`max_leverage=3.0`，证据 `shared/models/risk.py:21-38`。
- fixed Top20 medium preset：`max_leverage=10.0`、总敞口上限 0.50，证据 `shared/models/risk.py:41-59`；项目记忆进一步说明 BTC/ETH/SOL 与其他 Top20 的资产 tier 不同：`.github/agent/memory/project-memory.md:12-16`。

## 3. 文档、研究资产与代码一致性

### 3.1 明确过时的开发手册

`AI量化平台_开发协作Prompt手册 (1).md:282-285` 仍把“真实 Binance 数据接入 + Paper 端到端闭环”列为“Phase 2 暂不启动”。这与 TASK-039~045 的当前实现和验收证据冲突。该手册不能再作为新会话的项目状态真源；应保留为历史方法文档，状态判断以项目记忆、当前代码和新鲜门禁为准。

### 3.2 高质量报告中的有效缺口

`02_量化策略与LLM+RAG开平单逻辑详细报告.md` 对当前主链描述基本可信，但其 `0.48` “默认值”已经被自动策略显式 `0.50` 超越。报告暴露的以下缺口与源码一致：

- 缠论买卖点尚未实现，且策略库要求先定义客观算法：`策略库/04_风险控制与禁用清单.md:66-75`。
- 4h/15m 使用同一信号集合，尚未按周期职责拆分。
- RAG 为关键词匹配，不是向量检索：`services/agents/rag_context.py:19-42`。
- 组合相关性风险和净敞口仍待补：`策略库/04_风险控制与禁用清单.md:33`。
- Review Agent 的 failure summary 仍是规则聚合，不是真正 LLM 总结：`services/agents/service.py:261-272`；Review service 本身按失败记录聚合报告：`services/review/service.py:46-64`。

### 3.3 Jesse / NautilusTrader / Qlib / vectorbt / OpenBB 已有真实本地资产

这五个来源不是“报告声称已摄取但仓库无文件”：结构化 seed manifest 已登记 Jesse（`research_source/open_source_strategy_library/manifests/seed_sources.json:60-62`）、NautilusTrader（`:492-494`）、Qlib（`:569-571`）、vectorbt（`:644-646`）和 OpenBB（`:680` 起）。仓库中五个来源目录均同时存在 `asset_manifest.json` 与 `source_summary.md`：

- `research_source/open_source_strategy_library/assets/jesse/`
- `research_source/open_source_strategy_library/assets/nautilus_trader/`
- `research_source/open_source_strategy_library/assets/qlib/`
- `research_source/open_source_strategy_library/assets/vectorbt/`
- `research_source/open_source_strategy_library/assets/openbb/`

摄取代码会生成 source summary、asset refs 和 asset manifest：`research_source/open_source_strategy_library/ingestion.py:197-216`、`:286`。真实缺口是它们尚未全部进入 `策略库/01_外部策略来源索引.md` 的正式可读索引，后续任务B应同步结构化 manifest 与 Markdown，不应重复宣称“首次摄取”。

## 4. TODO / FIXME / stub / mock 复核

本轮关键词搜索未发现会伪造核心交易结果的生产 `TODO`/`FIXME` 或直接返回固定交易结论的 stub。

需要分类而不能误报的命中：

- `Binance Mock Trading` 是 Binance 官方模拟交易环境语义，`shared/config.py:86-88` 明确区分 `demo` 与 legacy `testnet`，不是测试替身。
- `vi.mock`、`mockImplementation`、Python `unittest.mock` 等集中在测试文件，是隔离 API/LLM/网络依赖的正常测试手段。
- 通知渠道 `stub` 和 LLM provider `stub` 出现在定向测试夹具，不是生产执行器。
- `tests/api/test_execution_runtime_api.py` 的 `notes=["stub reconcile"]` 是测试数据文字，不是生产对账桩。
- `docs/architecture/strategy-library-collection-and-scoring.md:58` 将 WorldQuant 本地 alpha 扫描标为接口接缝，属于公开记录的未完成研究能力，不在当前交易执行主链。
- Freqtrade 配置在 `shared/config.py:143-146` 明确标注 “declared but not yet wired”，是后续集成项。

测试中的大量 mock 意味着网络、凭据、Docker/Compose 和长期运行仍必须单独做集成/运行时验收；它不否定本轮单元与轻量集成门禁，但也不能据此宣称所有外部依赖已经生产验证。

## 5. 风险分级与后续路线图输入

### 核心阻塞

无。当前主链未发现绕过 Validation、无止损开仓、LLM 决定方向/价格/仓位，或启用 gateway 时本地先落账的证据。默认值冲突有清晰覆盖顺序，不导致运行行为不可判定。

### 高优先级但非阻塞

1. 建立 Playbook 的配置作用域模型，分别展示 `0.48/0.50/0.45` 和 `5x/10x/3x/资产 tier`，并附源文件引用。
2. 让 4h 方向周期与 15m 入场周期支持不同信号子集，再做 OOS 回测；当前同集合确认可能偏离运营者真实手法。
3. 将 MetaLabel 从短窗规则近似升级为有样本外证据的统计/模型流程，保留 cold-start fail-closed。
4. 增加单 symbol 总风险、跨品种相关性和组合净敞口风控；不得只用逐订单 exposure 上限替代组合风险。
5. 将 Review Agent 从规则聚合升级为受结构化 schema 约束的 LLM 归因，并保持规则结果为事实底座。

### 中优先级技术债

1. 缠论先完成客观算法定义和离线人工标注对照，再作为第九类独立信号接入。
2. 当研究资产规模和问答范围扩大时，将关键词 RAG 升级为可评测的向量/混合检索；当前二元 veto 场景无需为技术名义提前引入复杂基础设施。
3. 修复全仓 65 个文件的格式漂移应单独立项、单独 diff，不与功能变更混做。
4. 更新或归档过时 Prompt 手册的阶段状态，防止下一轮 Agent 误判项目仍处 Phase 2 前。

## 6. 复核边界

- 本报告不把历史记忆中的测试数字冒充本轮结果；第 1 节采用本轮统一执行得到的最新门禁结果。
- 本报告没有重新发起真实 Binance 网络交易，也不因此新增任何外部成交声明。
- `253 passed` 的测试集合包含 mock/fixture 驱动场景；真实网络、凭据、Spot Testnet、Docker/Compose、长时间 scheduler/Celery soak 仍需按各自验收任务提供新鲜证据。
- 本报告没有修改策略信号、MetaLabel、组合风控算法，也没有批量格式化历史文件。

## 7. 任务A/B完成后的复验

- 新增 Playbook、路线图持久化、生态 manifest 合同与迁移测试后，后端全量为 `257 passed, 1 skipped, 2 warnings`。
- 前端在补齐七个说明 Tab、策略资产回归、路线图成功/失败和 Playbook 错误重试后，最终全量为 `11 files / 30 tests`。
- Ruff、mypy（119 source files）、生产 build、`git diff --check` 均通过；全新 SQLite 从 `0001` 迁移到 `0007`。
- 本节是迭代完成后的验证附录，不覆盖第 1 节任务〇的原始 `253 passed` 基线。
