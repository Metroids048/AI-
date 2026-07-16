# 发现与决策

## 需求

- 用户报告自动开平仓系统与量化策略逻辑存在严重缺陷，要求先给独立分析，再按第 3 点方案/Prompt 顺序逐模块修复，不确定处必须先问。
- Phase 1 的具体实现方式已被用户明确授权由我决定（"这部分你定吧"），但每步完成后必须同步更新全部项目文件。

## 研究发现（Phase 1）

- `shared/models/workflow.py` 中 `AutoTradingSettings.asset_risk_tiers` 使用 Pydantic `default_factory`，导致该字段**始终非空**（默认含 core/standard 两档）。
- `services/execution/paper_signal.py::_requested_leverage`/`_requested_notional` 只要 `execution_profile.get("asset_risk_tiers")` 为真值就无条件优先于 `position_rules.max_leverage`——而这个条件因上述默认值永远为真，是"杠杆滑块不生效"的架构根因。
- 前端 `RuntimePanels.jsx` 的 `AutoSettingsPanel` 没有任何分档编辑 UI，只有一个绑定 `max_leverage` 的全局杠杆输入框（这是一个从未被 `asset_risk_tiers` 读取的死字段）。
- `DEFAULT_AUTO_SETTINGS.asset_risk_tiers` 前端硬编码值（core=10x/15%，standard=5x/6%）与后端真实默认值（core=20x/15%，standard=10x/6%）不一致，证明前端从未真正反映过后端实际生效值。
- 前端"档位预览"区域是纯静态文案（硬编码"10x·单币15%"/"5x·单币6%"），不随任何用户输入变化，是纯装饰性展示，与真实状态完全脱钩。
- 每周 Celery 任务 `refresh_volatility_asset_risk_tiers`（`services/execution/tasks.py`）会用 ATR% 三分位动态计算 `vol_low`/`vol_mid`/`vol_high` 分档并写回运行中 PaperRun 的 `execution_profile["asset_risk_tiers"]`——这意味着分档表不只有 core/standard 两档，任何修复方案都必须兼容动态分档场景。
- `services/execution/paper_runtime.py::_apply_trailing_ratchet`（通用 `ProtectiveLevels` 抽象）与 `services/validation/technical_replay.py::_advance_open_position`（手写 `ExitLadderState`/`_OpenPosition` 重建）在"阶梯止盈全部执行完毕后的余量移动止损"逻辑上，经逐行核对，数学上完全等价：均为"价格向有利方向移动量 ≥ trail_after_r × initial_distance 后，将止损棘轮式收紧到入场价"，只是分别作用在两种不同的数据结构上，属于纯粹的代码重复而非行为分歧。

## 技术决策

| 决策 | 理由 |
|------|------|
| 采用方案 B：滑块联动分档表 | 用户通过 AskUserQuestion 明确选择；保留 core/standard/vol_* 分档结构本身的语义价值（区分核心币与其余币种风险等级），同时让操作员的杠杆/敞口滑块真正驱动生效值。 |
| 新增 `scale_asset_risk_tiers()` 而非直接覆写单一杠杆值 | 需要保留每周 ATR% 动态分档任务写入的 symbol 分配（`vol_low`/`vol_mid`/`vol_high`），只重新计算数值（`leverage`/`max_position_fraction`），不破坏分档结构本身。 |
| `TIER_SCALE_RATIOS` 按相对风险排序锚定 core=滑块原值 | core 档直接等于操作员设定的杠杆/敞口上限，其余档位按预定义比例收紧，维持 core > vol_low > standard > vol_mid > vol_high 的风险梯度。 |
| 提取共享纯函数 `next_trailed_stop_price()` 到 `exit_ladder.py` | 该模块已是双方都可无循环依赖导入的纯函数模块；提取后 `paper_runtime.py` 与 `technical_replay.py` 共用同一套棘轮止损数学，避免未来只改一处导致回测与实盘行为分歧。 |
| 前端预览改为动态计算而非静态文案 | 让操作员在保存前就能看到滑块变动后实际会生效的分档数值，避免"看起来没变化"的误导。 |

## 遇到的问题

| 问题 | 解决方案 |
|------|---------|
| 最初误判 `asset_risk_tiers` 从未写入 `execution_profile` | 通读完整 Pydantic 模型与前端代码后修正：字段每次保存都会写入，但因前端从未真正编辑它而写入的是陈旧/脱钩的默认值——已向用户澄清更精确的诊断。 |
| 需要在不破坏动态 ATR% 分档场景的前提下重新计算数值 | `scale_asset_risk_tiers()` 以现有分档表（或默认表）为基底，只重写 `leverage`/`max_position_fraction` 两个数值字段，symbol 分配原样保留。 |

---

## 发现与决策：第零节 —— LLM 决策否决链路

> 独立任务线，见 `task_plan.md` 顶部说明。一~五节仍在范围外。

## 研究发现（第零节）

- `services/agents/llm_factory.py::build_configured_llm_runtime()` 在所有 provider（Anthropic/OpenRouter/GitHub Models）均未配置成功时静默返回 `UnavailableLLMRuntime`，不打任何日志；`services/agents/service.py::_execute_llm_veto` 原来的笼统 `except Exception` 也不区分失败类型——两者叠加导致"LLM 否决链路从未被真正调用"这一现象在日志/报错层面完全不可见，只能靠追代码路径才能发现。
- `shared/config.py` 的 `claude_api_key` 字段此前只读 `CLAUDE_API_KEY` 一种环境变量命名，用户实际配置的是 `ANTHROPIC_API_KEY`，属命名不一致导致的静默未配置。
- `services/execution/bootstrap.py::_ensure_auto_paper_run` 的 `preserved_keys` 元组原本不含 `"llm_veto_enabled"`，导致运营手动关闭 LLM 否决后，每次 bootstrap 重启都会被硬编码的 `True` 悄悄改回启用——这是一个与"链路从未被调用"同源但方向相反的独立 bug（该字段本身没被链路读取问题掩盖过，只是恰好在同一次审计中被发现）。
- 端到端连通性验证时发现 `_OPENROUTER_SEED_MODELS` 硬编码的两个免费模型 ID 已被 OpenRouter 下架（返回 404，错误信息里带"改用 X"提示），这与用户最初报告的静默失效是**同一类症状的另一个独立根因**——即使前五点代码修复全部落地，只要种子模型列表本身失效，验证仍然会在最后一步失败。通过 `discover_openrouter_free_models()` 查询实时目录并逐个试跑，确认 `nvidia/nemotron-3-super-120b-a12b:free`、`nvidia/nemotron-3-nano-30b-a3b:free` 能稳定返回可解析 JSON（部分候选模型如 `nvidia/nemotron-nano-9b-v2:free` 在 `max_tokens=400` 下因推理 token 耗尽返回 `content: null`，另有候选返回 429/400，均已排除）。
- GitHub Models 的两套 API 表面（旧版 `models.inference.ai.azure.com`、新版 `models.github.ai/inference`）对同一个 token 分别返回 401（"The `models` permission is required"）与 403（"no_access"），但该 token 能正常访问目录列表 GET 接口（`models.github.ai/catalog/models` 返回 200）——这组合精确指向"token 本身缺少 Models 读取权限范围"，而不是模型名称错误或代码调用方式错误，属于运营侧凭证配置问题，非代码缺陷。

## 技术决策（第零节）

| 决策 | 理由 |
|------|------|
| 用 `AliasChoices` 而非新增独立字段兼容两种环境变量名 | Pydantic Settings 原生支持多别名读取同一逻辑字段，避免在代码里到处判断"到底读哪个变量名"。 |
| 异常拆分为 `TimeoutError`/`RuntimeError`/`Exception` 三支而非维持单一 `except Exception` | 否则运营看到的永远是同一种笼统失败原因，无法区分"provider 超时"、"provider 主动拒绝/额度耗尽"、"未知代码异常"这三类需要完全不同应对方式的失败。 |
| 新增 `GET /agents/llm-status` 而非要求运营翻日志 | 结构化状态查询比日志排查更适合日常巡检，且不依赖日志保留策略/日志级别配置。 |
| 发现 OpenRouter 种子模型过期后直接替换，而非只报告"这是 OpenRouter 的问题" | 免费额度端到端验证是用户本节的硬性验收标准；模型下架导致的失败和用户最初报告的"链路静默失效"现象在运营视角是同一件事（"配置了但用不了"），必须一并解决才能真正闭环。 |
| GitHub Models 遇阻后用 AskUserQuestion 询问而非自行决定跳过或自行想办法绕过权限限制 | Token 权限范围是账号侧配置，不是我能通过代码修改绕过的问题；按"任何不确定必须先问用户"的标准规则，将"继续排查 token 权限 vs 跳过只用 OpenRouter 验证"这一选择权交给用户，而不是擅自决定验收范围。 |

## 资源（第零节）

- `shared/config.py`（`claude_api_key` 字段 `AliasChoices`）
- `services/agents/llm_factory.py`（provider 日志、`_OPENROUTER_SEED_MODELS`）
- `services/agents/service.py`（`_execute_llm_veto` 三支异常处理）
- `services/agents/llm_runtime.py`（`FallbackChainStructuredLLMRuntime`、`LLMProviderUnavailable`、`discover_openrouter_free_models`/`discover_github_models_free_models`）
- `services/execution/bootstrap.py`（`_ensure_auto_paper_run::preserved_keys`）
- `apps/api/routers/agents.py`（`GET /agents/llm-status`）
- `tests/integration/test_llm_decision_veto_live.py`（新增端到端集成测试，`RUN_LLM_VETO_INTEGRATION=1` 门控）
- `tests/integration/test_binance_public_smoke.py`（参照的既有 opt-in 集成测试约定）
- `tests/conftest.py`（`db_session` SQLite harness fixture）

## 资源

- `shared/models/workflow.py`（`AssetRiskTierSettings`、`AutoTradingSettings`）
- `services/execution/risk_tiers.py`（`scale_asset_risk_tiers`、`TIER_SCALE_RATIOS`、`default_asset_risk_tiers`、动态 ATR% 分档）
- `services/execution/paper_signal.py`（`_requested_leverage`/`_requested_notional` 根因所在）
- `services/execution/exit_ladder.py`（新增 `next_trailed_stop_price` 共享纯函数）
- `services/execution/paper_runtime.py`（`_apply_trailing_ratchet`、`_resolve_protective_levels`）
- `services/validation/technical_replay.py`（`_advance_open_position` 阶梯余量移动止损分支）
- `apps/api/routers/runs.py`（`update_paper_run_auto_settings`）
- `frontend/admin/src/components/RuntimePanels.jsx`（`AutoSettingsPanel`、`DEFAULT_AUTO_SETTINGS`、`renderTierPreviewRows`）
- `tests/services/test_asset_risk_tiers.py`、`tests/services/test_exit_ladder.py`、`tests/api/test_paper_runtime_api.py`

## 研究发现（Phase 2/3）

- **Phase 2 第 1 项（默认限价单）**：核查 `shared/config.py:58`、`services/execution/paper_signal.py:82`、`services/execution/gateway.py:339` 后确认**已实现**，无需改动。`execution_default_order_type="limit"` 是平台级默认值，`paper_signal.py`/`gateway.py` 均以 `strategy.rules.entry_rules.get("order_type", settings.execution_default_order_type)` 兜底取值。`gateway.py::submit_acceptance_order`/`submit_carry_order` 硬编码 `"market"` 是刻意行为——分别是 Testnet 验收探测单与资金费率搬砖腿，不是方向性策略入场单，不违反"默认限价单"的要求，反而正好体现"保留可配置市价单兜底"这一诉求。
- **Phase 2 第 2 项（收紧反向信号平仓逻辑）**：核查 `services/execution/paper_runtime.py::run_cycle`（约 606-830 行）确认同一根K线内的执行优先级已经是：保护性触发（止损/止盈，含 `_apply_trailing_ratchet` 棘轮移动止损）先判定并 `continue`，反向信号平仓分支（`close_on_opposite_signal` 判断，746 行起）严格排在其后才可能触达。这与 `docs/audits/2026-07-12-current-state-review.md` 第 2.8 节记录的既有结论一致。此项的真实交付是补充回归测试 `test_runtime_stoploss_wins_over_opposite_signal_hit_on_same_bar`（`tests/services/test_paper_runtime.py`）锁定该行为，而非改变行为本身。
- **Phase 2 第 3/4 项（ADR-023 重复编号与 5%/15% 数值不一致）**：确认已在此前会话中修复：重复的 "ADR-023" 条目已重新编号迁移为 ADR-058，并将原文档中错误的"5% 组合初始风险"文字修正为与 `services/execution/bootstrap.py::AUTO_PAPER_TECHNICAL_RULES` 实际值一致的 `max_portfolio_initial_risk_fraction=0.15`（15%）。本轮通过重新读取 `.github/agent/memory/decisions-log.md` 第 1-9 行确认该修正仍然完整生效。
- **Phase 3（AND-gate → 多数票）**：核查 `services/strategy_library/ensemble.py::layered_regime_entry` 确认已实现多数票裁决机制：方向类信号源（`technical_dow_trend`/`technical_ema_trend`/`technical_adx`/`technical_mtf_ma`/`technical_macd` 等）参与投票，`MIN_DIRECTION_SOURCE_QUORUM=3`，仅在票数不足 quorum 或方向票数恰好打平时才 fail-closed，不再要求全部信号源一致同意（旧的全票 AND-gate 逻辑已被取代）。`tests/services/test_signal_ensemble.py` 覆盖了多数票通过、平票 fail-closed、quorum 不足 fail-closed、少数反向票被多数碾压等场景，全部通过。
- **结论**：Phase 2、Phase 3 在本轮排查中均确认"已被此前会话完整实现"，本轮的实际工作是核查验证 + 补齐回归测试锁定行为 + 同步项目跟踪文件（`task_plan.md`/`findings.md`/`progress.md`/`project-memory.md`），不是从零实现新逻辑。

## 研究发现（Phase 4）

- **探测单/搬砖单隔离机制核查**：逐行核对了 `services/execution/gateway.py::BinanceUsdtPerpetualGateway`（325-524 行，`submit_order`/`cancel_order`/`reconcile`/`set_leverage`/`refresh_protection_orders`/`_submit_protection_algo_orders` 全部方法）与 `services/execution/carry_execution.py::CarryExecutionService`（全文 234 行），确认资金费率搬砖两腿**从未调用** `ExecutionRepository.create_order()`（`grep execution_repo|ExecutionRepository|OrderExecution` 在 `carry_execution.py` 中无匹配）。搬砖结果只以 `CarryExecutionLegResult` 形式存在于 `CarryExecutionStatus` 响应体内，经 `apps/api/routers/runs.py::create_carry_execution` 里的 `AgentTaskRepository.update_task(output_payload=...)` 落库——完全绕开 `OrderExecution` 表。
- 真实策略的绩效统计（Review Layer 复盘、策略淘汰判断等）只读取 `execution_repo.list_orders()`/`OrderExecution` 表，从不读取 `AgentTaskRepository` 的 `output_payload`——因此搬砖腿在架构上天然不会计入任何策略绩效指标，无需额外打标签。
- 这与 Testnet 验收探测单（`demo_audit.py::BinanceDemoAuditService`）的隔离方式不同：验收探测单**会**落地为真实的 `OrderExecution` 行（因为需要验证真实下单链路），所以必须靠专属的 `BINANCE_DEMO_AUDIT_KEY` Strategy/BacktestRun/PaperRun 三件套 + `entry_context.execution_kind`/`strategy_performance_eligible=False` 显式标签来隔离；搬砖腿则是靠"从不落地为 `OrderExecution`"这一更彻底的架构隔离，两种机制并存但服务于同一目标（真实策略绩效不被非策略性订单污染）。
- **ExitLadder 端到端测试缺口核查**：确认此前只有服务层直接调用测试（`tests/services/test_paper_runtime.py` 用 `_runtime_with_position()` 直接注入 `OrderExecution`/`PositionSnapshot`，绕开决策管线）与回测重放层测试（`tests/services/test_technical_strategy_validation.py`），**没有**任何测试通过真实 HTTP `/auto-cycle` 接口 + 真实决策管线走满"建仓 → 触发阶梯止盈部分平仓"的完整闭环。
- 通过真实决策管线建仓时，入场价/止损价由 `PaperSignalGenerator._risk_prices()` 动态计算（ATR 或规则优先级：`fixed_bps` > `basis_bps` > `atr_multiple*ATR` > `max_net_loss_bps` > 默认 1.5%），若不加约束会导致测试无法提前预知精确的止损距离。解决方案：给测试策略显式配置 `stoploss_rules={"fixed_bps": 200}`，跳过 ATR 优先级分支，使止损距离变为 `entry_price * 200bps` 这一测试代码里可提前算出的确定性值；入场价则取 `_store_trend_bars` 构造的最后一根 1h K线收盘价（决策管线的 `reference_price` 直接取自最新收盘K线）。由此可在不读回接口响应的前提下，提前计算出阶梯止盈 L1 的触发价（`entry + risk_distance * r_multiple`），构造能精确命中该价格的第二轮 1 分钟保护K线。

## 技术决策（Phase 4 追加）

| 决策 | 理由 |
|------|------|
| Phase 4 第 1 项（订单来源隔离）判定为"无需改代码" | 追踪完整调用链后确认搬砖腿从架构上就不产生 `OrderExecution` 行，绩效统计管道读不到它，属于比显式标签更彻底的隔离；新增标签只是重复劳动，不产生任何新的正确性保证。 |
| 新 ExitLadder 端到端测试采用"确定性 `fixed_bps` 止损"而非"读回接口响应反推止损价" | 前者让测试代码本身能独立算出触发价，测试意图更清晰、对将来风险价格计算逻辑变化更稳健；后者需要额外一次接口调用/仓位查询才能拿到止损价，增加测试复杂度且未带来额外覆盖价值。 |
| 扩展 `_create_validated_paper_run()` 增加可选 `stoploss_rules`/`takeprofit_rules`/`strategy_key` 参数，而非新建一个平行的建仓辅助函数 | 默认值与原硬编码值完全一致，不影响任何既有测试；复用现有的策略/假设/回测/PaperRun 创建链路，避免重复代码。 |

## 资源（Phase 4 追加）

- `services/execution/gateway.py`（`submit_order`、`submit_carry_order`，确认无 `ExecutionRepository` 调用）
- `services/execution/carry_execution.py`（`CarryExecutionService`，全文核查确认无订单持久化）
- `services/execution/demo_audit.py`（`BinanceDemoAuditService`，验收探测单隔离标签来源）
- `services/execution/exit_ladder.py`（阶梯止盈触发/平仓数学，`level_trigger_price`/`level_hit`/`apply_level_fill`）
- `services/execution/paper_runtime.py`（`run_cycle` 阶梯止盈判定分支、`_ensure_exit_ladder`）
- `services/execution/paper_signal.py`（`_risk_prices`/`_stop_distance`/`_take_distance`，决定测试所需的确定性止损配置方式）
- `tests/api/test_paper_runtime_api.py`（新增 `test_paper_runtime_auto_cycle_partial_closes_via_exit_ladder`）

## 研究发现（Phase 5）

- **跨品种相关性组合风控已存在，非缺口**：`services/execution/portfolio_risk.py` 提供三个纯函数——`correlation()`（Pearson系数，样本长度不等或<2或方差为0返回`None`）、`close_returns()`（要求至少61根收盘价才产出60个收益率，否则返回`None`，用于fail-closed）、`signed_exposure()`（按`TradeSide`带符号的名义敞口占账户权益比例）。
- `services/execution/paper_signal.py::_build_risk_state()`（391-486行）是实际调用点：对候选品种与每个已持有品种（同品种跳过）各取最近61根1h K线算收益率相关系数；同方向且系数>0.70则计入`high_correlation_peer_count`/`correlated_cluster_exposure`；任何一次相关系数计算返回`None`（数据不足）都会把`correlation_available`置为`False`并保持`False`（fail-closed，不会被后续成功的计算翻转回`True`）；存在同方向高相关对手仓位时，用`max(0.5, 1-max_peer_correlation)`折算候选订单名义本金（0.5地板值是Thread A修复的一部分，避免与gatekeeper双对手强拒绝叠加导致临界单被压缩至近零，见此前`task-history.md`记录）。
- `services/execution/gatekeeper.py::_evaluate_numeric_risk()`（203-213行）是最终拒绝点：`portfolio_correlation_available=False`→拒绝`portfolio_correlation_unavailable`（仅新开仓生效，`close_only_mode`不受影响，因为平仓不应被数据不足卡住）；`high_correlation_peer_count>=2`→拒绝`correlated_exposure_limit_exceeded`；`correlated_cluster_exposure+requested_fraction>0.35`→拒绝`correlated_cluster_exposure_exceeded`；`abs(net_directional_exposure+requested_signed_fraction)>0.40`→拒绝`net_directional_exposure_exceeded`。
- 追溯 `.github/agent/memory/task-history.md` 的 `[TASK-048] ExitLadder + correlation tighten + reconcile decouple + Binance sim smoke` 条目，确认这套相关性风控（含fail-closed设计与双对手强拒绝）是在更早的一轮任务中就已完整实现，早于本轮用户报告与Phase划分——**Phase 5并非新增架构缺口，而是已落地能力，用户报告中"没有考虑相关性"的表述与代码现状不符**。
- 测试覆盖核查：`tests/services/test_execution_gatekeeper.py` 中 `test_gatekeeper_rejects_two_high_correlation_peers`（双对手强拒绝）、`test_gatekeeper_rejects_correlated_cluster_and_net_directional_exposure`（簇敞口+净方向敞口双拒绝）、`test_gatekeeper_rejects_missing_portfolio_correlation_for_new_order_but_not_close`（数据不足fail-closed，且区分新开仓/平仓）、`test_portfolio_return_correlation_requires_full_60_bar_window`（60根窗口边界，系数计算正确性）——四条拒绝路径与关键边界均有测试锁定。
- `services/strategy_library/playbook.py` 中的运营路线图项 `portfolio-correlation-risk`（P0，描述"补齐单品种、相关性簇和组合净敞口限制"）所描述的能力已被上述实现完整覆盖；该路线图状态字段（`StrategyRoadmapState.status`）默认值为`pending`且需运营手动通过API更新，与代码是否已实现无关——路线图状态未同步不代表功能未完成，只是运营台账未勾选。

## 技术决策（Phase 5 追加）

| 决策 | 理由 |
|------|------|
| Phase 5 判定为"已实现，无需新代码"，仅做核查+归档 | 完整追踪 `portfolio_risk.py`→`paper_signal.py::_build_risk_state`→`gatekeeper.py::_evaluate_numeric_risk` 调用链与四个既有测试，确认相关性计算、折价、fail-closed、双维度（簇敞口+净方向敞口）拒绝均已到位且被测试锁定；该实现早于本轮任务（见`TASK-048`），新增代码只会造成重复实现或语义冲突。 |
| 不修改 `StrategyRoadmapState` 路线图状态字段 | 该字段是运营台账，需人工通过 `PATCH` API 更新为`done`并附审计记录，不属于本轮代码修复范畴；擅自改库内种子数据的状态字段既不符合API语义（缺少`RoadmapUpdate`审计上下文），也超出"修复自动交易系统缺陷"的任务边界。 |

## 资源（Phase 5 追加）

- `services/execution/portfolio_risk.py`（`correlation`/`close_returns`/`signed_exposure`）
- `services/execution/paper_signal.py`（`_build_risk_state`，391-486行）
- `services/execution/gatekeeper.py`（`_evaluate_numeric_risk`，203-213行）
- `shared/models/workflow.py`（`ExecutionRiskState`，336-356行）
- `tests/services/test_execution_gatekeeper.py`（四条既有测试，核查覆盖完整性）
- `.github/agent/memory/task-history.md`（`TASK-048`条目，确认实现时间点早于本轮任务）
