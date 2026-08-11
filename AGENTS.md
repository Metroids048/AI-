<!-- ai-quant-research-platform: v0.1 -->
# AI Quant Research Platform

> **本文件分五层，每层修改门槛不同。改动前先确认你在哪一层。**
>
> | 层 | 内容 | 修改门槛 |
> |---|---|---|
> | 1. North Star | 产品定位、Exchange-First 不变量、六层架构定义 | **高**：需操作员显式确认"变更不变量" |
> | 2. Definition of Done | 按模块类型区分的验收标准 | 中：新增模块类型时补充 |
> | 3. Enforcement | 指向 hooks / CI / pre-commit 的索引 | 中：机制变更时同步 |
> | 4. Current Runtime Baseline | 风控数值、当前候选、当前阶段 | **低**：随时可改，且随时可能过期 |
> | 5. Working Rules | 协作规则中无法被机制替代的部分 | 中 |
>
> 第 4 层的内容**随时可能过期**。当前事实的权威顺序永远是：
> 代码与测试 → 运行时数据库/调度状态 → `CURRENT_STATE.md` → 本文件 → ADR/归档。
> 第 4 层过期不代表该规则失效，只代表数值需要重新核对。

# 第 1 层 · North Star（不变量）

以下内容极少修改。任何改动都必须先向操作员说明"这是在变更不变量"并取得
明确同意，不得在实现任务中顺手调整。

## Canonical Source

- 主架构真源： [AI_Quant_Research_Platform_完整报告.docx](AI_Quant_Research_Platform_完整报告.docx)
- 本仓库中的一切实现、拆分、命名、阶段规划，都必须优先服从这份报告。
- 如代码实现与报告冲突：先修正实现方案，再更新代码；不得擅自降级为“简化版交易工具”。

## Product Identity

- 这不是“AI 帮我赚钱”的荐股或跟单工具。
- 这是一个 AI 驱动的量化研究平台，目标是持续生成、验证、淘汰、迭代交易策略。
- AI 的职责是研究、规则化、编码、分析、复盘与知识沉淀，不是主观预测涨跌。

## Non-Negotiables

1. 所有交易想法必须先规则化，再进入回测。
2. 所有策略必须经过：历史回测 -> 模拟盘 -> 小资金实盘。
3. 风控优先级永远高于收益。
4. Review Layer 不是附属报表，而是一级核心模块。
5. WorldQuant、GitHub、论文、A 股系统都是策略来源，不是平台主干。
6. 不允许跳过 Validation Layer 直接接 Execution Layer。

## Exchange-First Execution Invariant (Non-Negotiable)

The automated directional trading lane has one authoritative execution source: **Binance USDT-M Testnet / Binance Simulation**. The local SQLite/Paper subsystem is a projection, audit journal, strategy attribution store, and recovery cache. It is never proof that an exchange trade occurred.

1. The automatic execution universe is exactly `BTC/USDT` and `ETH/USDT` unless the operator explicitly changes it. Research coverage does not grant execution permission.
2. For an exchange-enabled automated run, the required sequence is: strategy decision -> Gatekeeper authorization -> Binance order submission -> Binance acknowledgement/fill -> local order/position/PnL projection.
3. A local position must not be opened, reduced, or closed before the corresponding Binance execution is confirmed. A local `accepted` status means only that the strategy/risk gate authorized an attempt.
4. Local entry price, exit price, filled quantity, fees, realized PnL, and timestamps must come from Binance execution data or subsequent exchange reconciliation. Strategy reference prices and OHLCV trigger prices are not execution truth.
5. When Binance and the local database disagree, Binance is authoritative. Reconciliation repairs or quarantines the local projection; it must never mutate the exchange merely to match stale local state.
6. Manual or exchange-only positions are `UNMANAGED_EXTERNAL_POSITION` until explicitly adopted. They must not inherit historical strategy protection records.
7. Local-only Paper execution is permitted only for explicit unit tests, deterministic replay, mocks, and offline research. It is not the default or acceptance path for the 7x24 automated BTC/ETH runtime.
8. Testnet acceptance/canary orders must be tagged and excluded from strategy performance. They prove connectivity, not strategy profitability.
9. Fixed BTC/ETH position, leverage, stop-loss, take-profit, Gatekeeper, and net-edge settings remain owned by the existing runtime configuration. Agents must not replace them with generic dynamic sizing or new symbols without explicit operator approval.
10. Future Agent work must not describe Binance as a downstream “mirror” of local Paper state. Legacy `Paper*` names are compatibility names only; their exchange-enabled behavior must obey this invariant.
11. The validated primary directional candidate remains the default. On exact-scope BTC/ETH Binance Testnet only, when that primary is silent because of `technical_signals_insufficient`, `multi_timeframe_disagreement`, `ensemble_discarded`, or `meta_label_bet_skipped`, the runtime may evaluate the existing `operator_heuristic_v2_relaxed` candidate as a bounded sampling fallback. It must be tagged `simulation_sampling_fallback`, retain the current fixed position/leverage/stop/take-profit and all account-risk gates, never run on mainnet or local-only Paper, and be reported separately from primary-candidate performance.
12. Packaged active-manifest strategy rules are the runtime source of truth. Bootstrap must stage rule drift into the immutable ConfigSnapshot for the next cycle; silently preserving stale database rules is forbidden.

Completion evidence for automated execution must include a real Binance Simulation order ID and exchange fill/position evidence. Local rows, mock calls, acceptance orders, or a successful strategy decision alone are insufficient.

## Strategy Owns Geometry, Execution Owns Safety (Non-Negotiable)

Authorized by the operator on 2026-08-11 as an explicit Layer-1 change. Full reasoning and
code anchors: [ADR-004](docs/adr/ADR-004-strategy-owns-geometry-execution-owns-safety.md).

**Buy/sell levels, stop placement, and profit targets belong to the Strategy Layer and must
come from quantitative rules that can be backtested, replayed, and falsified. Only
account-survival boundaries stay hard-configured. Execution never invents geometry.**

1. Strategy owns: direction, entry timing and confirmation, stop/invalidation price, target
   prices, partial-exit fractions, and whether to keep holding a runner.
2. Hard limits own: max risk per trade, max position fraction, max total exposure, max
   leverage, max entry price drift, stop-must-exist, duplicate-entry prohibition, and
   no-entry-while-reconciliation-unhealthy. A strategy may never raise these.
3. Geometry that violates a hard limit is **rejected with a visible reason**, never silently
   clamped into compliance.
4. `AUTO_EXECUTION_CHAIN` is `FROZEN_KNOWN_GOOD`: the Binance adapter, execution intent,
   order/fill persistence, managed position, ownership status, protection submission,
   reconciliation, recovery, emergency close, launcher, scheduler, and runtime state. Poor
   strategy performance is never a reason to modify it. Only a demonstrated execution defect
   is.
5. Protection prices are always resolved from the real exchange `average_fill_price` after
   fill confirmation — never from a strategy reference price or an OHLCV trigger price. This
   holds per leg if exits are laddered.
6. `testnet_sampling_v2` remains the running control lane with its exact current entry rules,
   `max(1.2 * ATR14, price * 0.0035)` stop, `1.5R` target, and current position/leverage/gate
   settings. Its current geometry is a **conservative sampling rule, not an endorsed long-term
   design**, and it is replaced only by a candidate that passed promotion — never by picking
   new percentages.
7. Forbidden: tuning the 0.35% stop floor, the 1.5R multiple, or any Validation Layer
   promotion threshold to change outcomes. Parameter substitution is not a strategy
   improvement, and swapping one hardcoded geometry for another does not satisfy this section.
8. Entry geometry and exit geometry must not be changed in the same evaluation step.
   Attribution requires that a measured difference be assignable to one of them.
9. One bounded exception to clause 4 is authorized: a **strictly additive** extension port for
   multi-leg (laddered) reduce-only exits. The existing single-target path must remain
   behaviorally identical, proven by regression tests. Building the port is not promotion;
   arming it for a candidate requires passing promotion.
10. Strategy comparisons must be post-cost and must report trade count, net expectancy, profit
    factor, average/median R, max drawdown, MFE, MAE, holding time, fee drag, and profit
    capture ratio (`realized profit / MFE`). Nominal RR and win rate alone are not evidence.

## Automatic Trading V2 Rebuild

**Status (2026-07-28):** V2 rebuild in progress (Task 0–18).

**Legacy Pipeline Freeze:** The following files are **frozen** — no new business logic may be added:

- `services/execution/paper_cycle_orchestrator.py`
- `services/execution/paper_exchange_execution.py`
- `services/execution/paper_order_lifecycle.py`
- `services/execution/paper_signal.py`

These files retain only:

- Verified ghost position guards
- Local Paper mode for offline research
- Historical data read paths
- Safe exit of legacy positions before cutover

**New V2 Implementation:** `services/automated_trading/` (domain, application, infrastructure, observability layers).

**Authoritative Design Source:** [docs/superpowers/plans/2026-07-27-automatic-trading-v2-final-rebuild-plan.md](docs/superpowers/plans/2026-07-27-automatic-trading-v2-final-rebuild-plan.md)

**Architecture:** [docs/architecture/automated-trading-v2.md](docs/architecture/automated-trading-v2.md)

**ADRs:**

- [ADR-001: Single Writer](docs/adr/ADR-001-automated-trading-v2-single-writer.md)
- [ADR-002: Exchange-First Receipts](docs/adr/ADR-002-exchange-first-receipts.md)
- [ADR-003: Entry Exit Gate Separation](docs/adr/ADR-003-entry-exit-gate-separation.md)

## Automatic Trading Completion Loop

The automatic trading system is not complete merely because code changed, tests
passed, a local Paper order exists, or execution reached an adapter.

For an automatic-trading task, continue this evidence-backed loop until the
requested real Binance Testnet proof exists or a strictly external blocker makes
it impossible:

OBSERVE -> trace a real scheduler cycle and candidate -> locate the earliest
failing boundary -> form one root-cause hypothesis -> reproduce it with a
failing test -> implement the smallest fix -> run focused and regression tests
-> restart the real API and RuntimeScheduler -> observe a new natural cycle ->
verify against Binance Testnet.

Binance Testnet is the execution source of truth. SQLite Paper state is only a
post-fill projection. A task requiring a complete automatic open/close lifecycle
may be marked COMPLETE only after a normal PAPER_SCHEDULER strategy entry has a
real Binance Testnet order ID and confirmed fill, a normal automatic exit has a
real reduce-only Binance Testnet order ID and confirmed fill, and both are
reconciled correctly into local state. Manual, acceptance, mock, direct-database,
local-Paper, and synthetic fast-round-trip evidence is invalid.

## Six-Layer Architecture

### 1. Data Layer

- A级市场数据：OHLCV、Volume、ATR、VWAP、EMA、Bollinger、RSI、MACD、ADX、Funding Rate、OI、Long/Short Ratio、Liquidation、Order Book
- B级宏观数据：FOMC、CPI、PPI、非农、利率决议、GDP、PMI、ETF 进展
- C级新闻数据：金十、Reuters/Bloomberg RSS、CoinDesk/The Block/Decrypt、A股新闻、SEC Filing
- D级社媒数据：重点账号事件流
- E级研究数据：GitHub、论文、Reddit、Telegram、YouTube、WorldQuant、本地 A 股系统

### 2. Strategy Layer

- Strategy Library 是平台长期核心资产。
- 禁止把策略逻辑散落在临时脚本中。
- 每条策略都必须具备：
  - `strategy_id`
  - `source`
  - `core_thesis`
  - `market`
  - `timeframe`
  - `market_regime`
  - `entry_rules`
  - `exit_rules`
  - `stoploss_rules`
  - `takeprofit_rules`
  - `position_rules`
  - `risk_level`
  - `backtest_status`
  - `paper_status`
  - `live_status`
  - `failure_reasons`
  - `iteration_history`

### 3. AI Agent Layer

- `Strategy Agent`
- `Coding Agent`
- `Backtest Agent`
- `Optimization Agent`
- `Research Agent`
- `News Agent`
- `Twitter Agent`
- `Telegram Agent`
- `Risk Agent`
- `Review Agent`

所有 Agent 必须通过结构化对象和任务通信，不得用“随手写文件”串联流程。

### 4. Validation Layer

- 历史回测
- 参数优化
- 样本外验证
- 模拟盘
- 小资金实盘

默认门槛：

- `Sharpe > 1.0`
- `Profit Factor > 1.3`
- `Max Drawdown < 25%`
- `Expectancy > 0`

不达标策略不得进入模拟盘。

### 5. Execution Layer

- Execution Engine
- Risk Engine

硬性规则：

- 没有止损的单子拒绝执行
- 禁止 Martingale
- 禁止因主观感觉人工加仓
- 禁止取消机器人止损

### 6. Review Layer

- 每日复盘
- 失效模式识别
- 策略暂停/调参/淘汰建议
- `failure_reasons` 与 `iteration_history` 回写
- 失败知识沉淀，供 Research/Strategy Agent 复用

## Required Tech Stack

- Python 3.11+
- CCXT
- Freqtrade
- Backtrader / VectorBT
- PostgreSQL
- Redis
- FastAPI
- Celery + Redis
- Docker Compose
- React + Tailwind
- Claude API
- LangChain / LlamaIndex

## Initial Market Scope

- 当前自动执行市场：`BTC/USDT`、`ETH/USDT` USDT-M 永续（Binance Simulation）
- 数据模型从第一天起必须支持未来扩展到：
  - `SOL`
  - `A股`
  - `美股`
  - `黄金`
  - `纳指`

## Target Repository Shape

```text
apps/
  api/
frontend/
  admin/
services/
  data/
  strategy_library/
  agents/
  validation/
  execution/
  review/
research_source/
  worldquant_adapter/
docs/
tests/
```

# 第 2 层 · Definition of Done（按模块类型）

"测试通过"不等于"完成"。不同模块的完成定义不同，取用对应那一条。

## Definition of Done — 策略 / 验证类改动

适用：`services/strategy_library/`、`services/validation/`、回测与指标计算。

- 必须给出**修复前 vs 修复后**的关键指标对比（Sharpe / PF / 胜率 / 期望 / 样本量），
  不能只贴"测试通过"。
- 指标类改动必须重跑对应的候选竞赛或回测脚本，贴真实数字。
- 样本量必须显式写出。35-65 笔 OOS 不足以支撑"策略有效"的结论，
  报告置信区间时必须说明区间是否跨越 0。
- **严禁为了让某个候选通过门槛而调整门槛数值本身**（`Sharpe > 1.0`、
  `PF > 1.3`、`MaxDD < 25%`、`Expectancy > 0`）。认为门槛不合理时，
  先停下来征得操作员同意，不得在同一个任务里顺手改掉。

## Definition of Done — 执行类改动（自动开平仓）

适用：`services/execution/`、持仓与保护记录、对账逻辑。

- 必须有**真实 Binance Testnet 订单 ID + 成交证据**。本地 Paper 行、mock 调用、
  验收单、纯代码审查都不算证据。
- 必须核对**本地持仓记录数 == 交易所持仓数**，并把两个数字都写出来。
  历史上出现过 37 条本地记录对 2 个真实仓位的"鬼持仓"。
- 涉及账户权益 / 回撤计算时，必须逐一确认三处独立调用点都已同步修改：
  `tasks.py` 的 risk_profile_sweep、`paper_signal.py` 的 `_build_risk_state`、
  `paper_cycle_orchestrator.py` 的 `_is_hard_drawdown_locked`。
  历史上漏改一处导致调度器卡死 38 分钟。
- 交付前必须用 Read 重新读取被改动的函数，确认函数体完整
  （历史上出现过"只加 import 没写函数体"导致 NameError）。
- 若改动需重启才生效：**自己执行重启并确认新值已生效**，不要把这一步丢回给操作员。

## Definition of Done — 前端改动

适用：`frontend/admin/src/`。

- 必须实际执行 `npm --workspace frontend/admin run test` 并贴真实输出。
- 必须启动 dev server 实际访问改动涉及的页面，确认：控制台无报错；
  网络请求频率正常（不是毫秒级轮询 —— 历史上 useEffect 依赖写错导致死循环，
  连续声称完成 3 次都没测出来）。
- 因环境限制无法验证时，必须显式写"以下结论未经浏览器验证，仅代码审查"，
  不得含糊。

# 第 3 层 · Enforcement（机制索引）

本层只做索引。规则细节由机制本身承载，不在文档里重复描述 —— 文档描述的规则
会被遗忘，机制不会。

## 机制清单

| 机制 | 位置 | 强制的规则 |
|---|---|---|
| SessionStart hook | `.claude/hooks/inject_context.py` | 会话启动/恢复/**上下文压缩后**重新注入核心约束 |
| PreToolUse hook | `.claude/hooks/guard_bash.py` | 拦截 `--no-verify`、mainnet/testnet 开关、迁移删除 |
| PostToolUse hook | `.claude/hooks/verify_critical_edit.py` | 编辑交易核心文件后自动跑 `-k` 测试子集并回灌真实输出 |
| Stop hook | `.claude/hooks/gate_stop.py` | 改了核心文件但无有效验证记录时**拒绝结束本轮** |
| pre-commit | `scripts/precommit_targeted_tests.py` | 暂存交易核心文件时强制跑匹配的测试子集 |
| pre-commit | `scripts/sync_skill_copies.py --check` | 三份 verify-work 副本与规范源一致 |
| CI | `scripts/refresh_current_state.py --check` | `CURRENT_STATE.md` 的 pytest 数字由真实运行生成 |
| 自检 | `.claude/hooks/selftest.py` | hook 契约本身可回归验证 |

配置入口：`.claude/settings.json`。

## 机制优先原则

新增强制性规则时，先问"能不能用 hook / CI / 测试表达"。只有确实无法机制化的
才写进本文档 —— 纯文档规则在长任务和上下文压缩后会失效，这是本仓库反复出现
返工的根因，不是执行者不够努力。

hook 用 Python 而非 bash+jq 实现：本机没有 `jq`，jq 版本会**静默失败**，
形成"看起来装了闸门、实际从不触发"的最坏情况。

# 第 4 层 · Current Runtime Baseline（易变数值）

**本层随时可能过期。** 数值以代码和运行时状态为准，本层只是索引和意图说明。

### 当前 Paper 阶段风险参数基线（2026-07-13 起生效）

- 这是"风控优先"原则下的**当前 Paper 验证阶段基线**，不是最终实盘阈值；目的是在模拟盘/测试网阶段获得足够的开单样本以验证信号与执行链路，实盘前必须重新收紧并走完 Validation Layer 门槛。
- 当前操作员选择的 Paper 采样档保持为：`risk_per_trade=0.05`、`max_portfolio_initial_risk_fraction=0.25`、`max_leverage=40`、`max_position_fraction=0.35`、`max_total_exposure=0.90`。这是模拟环境采样配置，严禁直接用于实盘。
- 手续费/滑点假设已对齐币安 USDM 合约常规用户真实费率（maker 2bps / taker 5bps，来源：https://www.binance.com/en/fee/futureFee），而不是凭空设置的保守估计；此前 10-18bps/边的假设是真实费率的 2-4 倍，导致 `net_edge_after_cost_negative` 门槛误杀大量本应通过的候选信号。调整手续费假设是为了让门槛准确反映真实成本，而不是放宽门槛本身去允许扣完成本预期为负的交易——`net_edge_after_cost <= 0` 拒绝入场这条规则本身保持不变。
- 杠杆/仓位比例（`max_leverage`/`max_position_fraction`/`risk_per_trade`）已按运营方要求上调至更激进档位（见 `services/execution/bootstrap.py` 中 `AUTO_PAPER_TECHNICAL_RULES`/`AUTO_PAPER_STRATEGY_RULES` 的行内注释与对应 ADR），用于在 Paper 阶段跑出足够密度的开平仓样本；止损/止盈/风控拒绝规则本身不受此调整影响。

### Current Truth And Archive Boundary

- 当前事实优先级：当前代码与测试 -> 运行时数据库/调度状态 -> `CURRENT_STATE.md` -> 架构与 ADR。
- `docs/archive/` 和 `scripts/archive/` 仅供历史审计；AI 不得把其中的诊断结论当作当前事实。
- 自动执行范围固定为 BTC/ETH；更广研究范围不得自动获得执行权限。主技术通道缺少匹配的 OOS evidence 时必须拒绝，观察通道不得作为真实交易或策略准入证据。

## Current Phase

- 当前阶段：`Phase 0 - 平台骨架与统一模型`
- 当前优先级：
  1. 全局项目配置
  2. 项目记忆体系
  3. 统一领域模型与仓库骨架
  4. API 与任务流
  5. 数据、验证、执行、复盘实现

<!-- AGENT-CONFIG-PACK:PROJECT-BRIDGE START -->

# 第 5 层 · Working Rules（协作规则）

保留无法被 hook 或 CI 替代的部分。凡是能被机制表达的，应迁移到第 3 层。

## Working Rules For AI Collaborators

1. 开工前先读：
   - `AGENTS.md`
   - `.github/agent/memory/project-memory.md`
   - `.github/agent/memory/decisions-log.md`
   - `.github/agent/memory/task-history.md`
2. 任何新增模块都必须说明它属于哪一层。
3. 任何代码改动都要说明它服务于哪一段研究闭环。
4. 不得把”先跑起来”当理由绕过风控、验证、复盘层。
5. 完工后必须更新记忆文件。
6. **禁止创建或切换分支/worktree**：所有工作必须在当前签出的分支（通常是 main）和本地工作目录中直接进行；禁止创建、切换、或建议切换到任何新分支或 git worktree，除非用户在当前这句指令里明确要求。
7. **低风险操作，可自主推进无需暂停确认**：阅读代码、编写/运行测试、运行已有的 lint/type/pytest 校验、更新文档、不涉及资金参数或风控阈值的内部代码重构（如拆分大文件、抽取函数）。
8. **高风险操作，必须先暂停并征得用户明确同意才能继续**：修改风控阈值/止损止盈/杠杆/仓位上限等数值；修改任何与交易所凭据、API Key 权限、mainnet 开关相关的代码；删除或回滚数据库迁移；修改 net_edge/gatekeeper 中的准入门槛数值。
9. **全仓 lint/type 存量问题不阻断目标文件验收**：全仓范围内已存在的、与当前改动无关的 lint/type 存量问题，不应阻断对当前改动所涉及文件的验收；但必须在交付报告中如实列出”本次未修复的存量问题”，不得隐瞒也不得因此暂停任务。
10. **交付自查规则（强制执行）**：交付前必须用 Read 工具重新读取所有被修改的关键代码段，逐一确认预期逻辑已落地，并在最终回复里显式列出自查清单和结果；禁止仅声称”已完成”而不提供自查证据。
11. **每次新会话/工作区必须立即运行 `pre-commit install`**：确认 `.git/hooks/pre-commit` 已写入后再进行任何代码改动；提交时钩子必须真正触发，**禁止 `git commit --no-verify`**。
12. **交付验证区块（强制格式，缺一项不得声称完成）**：
    ```
    [验证] ruff check .   -> <逐字贴最后一行，如 “All checks passed!”>
    [验证] mypy            -> <逐字贴最后一行，如 “Success: no issues found in N source files”>
    [验证] pytest -q       -> <逐字贴汇总行；如有 failed 必须列出失败测试名>
    [验证] git diff --stat -> <改动范围>
    [基线对比]             -> 本次是否引入新失败（若是，必须明确标注）
    ```
    禁止用”基本完成”或”应该没问题”替代真实数字。

13. **前端功能交付必须浏览器验证（强制执行）**：
    - 对于前端代码改动（React组件、hooks、API调用、状态管理等），**禁止仅通过代码审查就声称完成**
    - 必须实际启动前端开发服务器（`pnpm dev`），在浏览器中验证功能是否正常工作
    - 必须检查浏览器控制台是否有错误、警告、无限循环、网络请求异常
    - 必须检查网络面板，确认API请求频率正常（不是毫秒级疯狂轮询）
    - 必须在交付报告中明确说明：**已在浏览器中验证 <具体功能> 正常工作，无控制台错误，API请求频率正常（X秒/次）**
    - 如果因环境问题无法启动服务器，必须明确说明原因，并提供替代验证方案（如单元测试覆盖关键逻辑）
    - **严禁**说”应该没问题”、”理论上可以工作”、”代码逻辑正确”等未经浏览器验证的结论

14. **严禁多次声称”完成”后仍需返工（强制执行）**：
    - 如果同一个任务已经声称”完成”2次以上，但用户反馈仍有问题，**必须停止继续声称完成**
    - 此时应该：
      1. 承认之前的验证不足，向用户道歉
      2. 详细说明为什么之前的验证失败了（遗漏了什么检查？）
      3. 制定更全面的验证计划（包括所有必要的检查步骤）
      4. **只有在完成所有验证步骤后**，才能再次声称完成
    - 记住：**用户的时间比token更宝贵**，反复返工会严重损害信任
    - 如果连续3次声称完成后仍有问题，必须在 `docs/AGENT_LESSONS.md` 中记录失败原因和改进措施

## Agent Config Pack bridge (2026-07-22)

Shared cross-tool contract for this repo (Cursor / Codex / Claude Code):

- Global Working Agreement lives in user globals (`~/.codex/AGENTS.md`, `~/.claude/AGENTS.md`, Cursor `00-agent-working-agreement.mdc`).
- This file (`AGENTS.md`) is the **project SSOT**. Claude imports it via `@AGENTS.md` in `CLAUDE.md`.
- Tool patches: `.cursor/rules/00-core-workflow.mdc`, `.cursor/rules/10-verification.mdc`, `.claude/rules/testing.md`.
- Before claiming COMPLETE: use `verify-work` skill. Canonical source is `.agents/skills/verify-work/SKILL.md`; the `.claude/` and `.cursor/` copies are generated mirrors kept in sync by `scripts/sync_skill_copies.py` (enforced in pre-commit and CI). Edit the canonical file only.
- Analysis / planning / review-only requests: do not edit files.
- Max 3 auto-repairs per failing check; same failure twice without progress → stop and escalate with evidence.
- Never report unexecuted checks as passed. Prefer project-documented verify commands.
- Durable lessons only in `docs/AGENT_LESSONS.md` (no secrets, no temp task chatter).
- Substantial changes: independent read-only review via `.claude/agents/code-reviewer` when available.
<!-- AGENT-CONFIG-PACK:PROJECT-BRIDGE END -->

<!-- AI-KNOWLEDGE-MANAGED-START -->

## 共享用户记忆与项目知识

开始涉及本项目的非简单任务前，读取：

- `项目知识库/项目总览.md`
- `项目知识库/当前状态.md`
- `项目知识库/目标与验收标准.md`
- `项目知识库/开放事项.md`

默认收口模式；任务结束生成会话记录并调用中央同步；不得直接重写中央主知识库。

<!-- AI-KNOWLEDGE-MANAGED-END -->
