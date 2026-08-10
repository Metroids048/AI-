# T0_RESEARCH_INFRASTRUCTURE_FROZEN_PLAN

> 阶段：**只读方案设计**。本文件不改任何生产代码。
> 生成时间：2026-08-08。基线：HEAD `d2e00e2`，branch `main`。
> 上游：[2026-08-08-strategy-fusion-research-frozen-plan.md](2026-08-08-strategy-fusion-research-frozen-plan.md)
>
> **本文件第 0 节的 Authorization Boundary 是实现阶段的唯一授权边界。**
> 任何超出该边界的改动（扩 prompt、接 veto、调风控、缩 WFO 窗口）一律视为越界。

---

## 0. Authorization Boundary（操作员已冻结）

```
D-A = A
ONE-TIME CANONICAL MIGRATION

legacy reader fallback   = FORBIDDEN
collision precheck       = REQUIRED
backup                   = REQUIRED
writer pause             = REQUIRED
conflicting duplicate    = STOP


D-B = B_PRIME

AgentTask.output_payload  = AI RESULT SSOT
LlmInvocation             = ATTRIBUTION SPINE

add to LlmInvocation:
  agent_task_id                    ← 最重要
  candidate_id    where available
  strategy_lane   where available
  prompt_version

do not duplicate trade_review bias as second truth


D-C = VERIFY_GATE

48_MONTH_TARGET = CONDITIONAL_APPROVED

2022-08 BTC/ETH 5m probe PASS  → authorize I-3
probe FAIL                     → do not weaken RT-05
                               → return for protocol decision


ai_advisory_veto            = OFF
AI prompt/schema redesign   = OUT_OF_SCOPE_FOR_T0
```

### 0.1 SSOT 分层（D-A 的关键澄清）

操作员明确区分了两个此前被混为一谈的概念：

| 层 | 允许的形态 | 规则 |
|---|---|---|
| **DB / internal SSOT** | 只有 canonical（`BTC/USDT`） | 唯一 truth，不得有第二种形态 |
| **boundary converter** | 可接受 legacy-shaped input | 输出必须 canonical，允许幂等 normalization |

因此 `universe.py:112-127` 的 `platform_to_exchange_symbol()` / `exchange_to_platform_symbol()` **不是**要删除的第二套 truth——它们是 ingress/exchange-boundary defensive normalization。已核实两者都在剥掉 `:USDT` 返回 canonical platform symbol，方向正确。

> 上一轮我把这两处标成"连带风险、需一并梳理以防矛盾"，方向说反了。它们不是 DB truth 的竞争者，验收标准应是"边界层幂等、数据层唯一"。

### 0.2 T0 边界的负面清单

T0 是 `research infrastructure readiness`，不是 `AI Setup Reviewer strategy redesign`。以下**明确不做**：

- 扩大 AI prompt 字段
- 重新设计 AI output schema
- 调整 bias distribution
- 启用 `ai_advisory_veto`
- 修改 `testnet_sampling_v2` 交易规则或 Active 接线
- 修改 leverage / `risk_per_trade` / max exposure / drift
- 放宽 external position quarantine
- 缩短 WFO 窗口以让数据通过
- 修改 Gate17 / exchange-first / single writer

---

## 1. Baseline（重新同步）

```
HEAD                  d2e00e2
branch                main
runtime engine flag   shared/config.py:52  automated_trading_engine = "legacy" (default)
                      .env 读取被工具权限拒绝；实际运行态由 DB 证据推断（见 1.2）
DB path               .local_paper_console.db   (331.3 MB)
data root             ohlcv_bars / market_extras / v2_* / agent_tasks / llm_invocations
manifest path         docs/evidence/active-manifests/auto_paper_mature_templates.json
```

### 1.1 工作树状态

```
staged:      scripts/verify_gate17_final.py, tests/services/test_paper_bootstrap.py
unstaged:    .claude/settings.local.json, scripts/run_proposal_research_replay.py
untracked:   docs/superpowers/plans/2026-08-08-*.md (2)
             scripts/s1*.py (8, 上一轮 S1 研究脚本)
```

与上一轮审计基线相比无实质变化。仅重新映射 T0 相关文件，未重做全项目审查。

### 1.2 运行态推断依据

`v2_execution_cycles` 13,379 行，`execution_mode = BINANCE_TESTNET`，且 `v2_exchange_fills` 有真实 Binance order/trade id ⇒ 曾以 `v2_active` 运行。**未由 `.env` 直接证实**，此点在 T0 全程按"未验证"对待。

---

## 2. T0 Blocker Matrix

| ID | 阻塞 | 状态 | P0 实测 |
|---|---|---|---|
| T0-01 | `market_extras` symbol 写读不一致 ⇒ funding/OI 恒空 | **CONFIRMED** | P0-1：106,292 行全为 legacy，canonical 0 行，碰撞 0 |
| T0-02 | 历史仅 12.7 月 < WFO 所需 42 月；5m 完全缺失 | **CONFIRMED** | P0-2：2022-08 BTC/ETH 5m 真实可得 |
| T0-03 | AI 归因无法 join 到 V2 outcome | **CONFIRMED（成因已更正）** | bias 已落 `agent_tasks`，见 §4.3 |
| T0-04 | manifest 声明 ≠ 运行事实，`report_path: null` | **CONFIRMED** | `candidate_key` 13,367 行全 NULL |
| T0-05 | `net_edge_after_cost_bps` / `manifest_eligible` / `ai_advisory_veto` 无赋值点 | **CONFIRMED（legacy 已有实现）** | 见 §4.5 |
| T0-06 | Replay 泄漏与确定性 | **ALREADY_SATISFIED（一处待补）** | 见 §4.6 |

---

## 3. P0 Preflight 实测结果（本轮已执行，只读）

### P0-1 market_extras collision audit

```
mode=ro，零写入。
market_extras columns: time, symbol, funding_rate, open_interest,
                       long_ratio, short_ratio, liquidation_usd

legacy (:USDT) symbols : 10   rows = 106,292
canonical symbols      :  0   rows =       0

overlapping (symbol,time) pairs : 0
  payload equivalent            : 0
  payload CONFLICTING           : 0

P0-1 VERDICT: SAFE_PLAIN_MIGRATION
```

10 个 legacy symbol 各约 10,634 行，时间窗全为 `2026-07-26 05:12:45` → `08:32:20`（约 3.3 小时）。

**含义**：不存在 canonical 行 ⇒ 不可能有 `(symbol, time)` 唯一约束冲突 ⇒ 迁移是纯 UPDATE，无需 dedup，无需 merge 决策。操作员定的 `conflicting duplicate = STOP` 分支本轮不会触发，但**协议仍须保留**（迁移执行时数据可能已变）。

### P0-2 5m availability probe

只读 public market-data endpoint，无 auth、无 key、无 DB 访问。

第一次 probe 有 bug（testnet 成功即 break，从未真正测 mainnet，而判定函数在检查 `fapi` 前缀），已重跑并加价格真伪校验：

```
===== MAINNET  https://fapi.binance.com =====
  BTCUSDT: first_open=2022-08-01T00:00:00Z open=23290.10  price_plausible=True
  ETHUSDT: first_open=2022-08-01T00:00:00Z open=1677.63   price_plausible=True

===== TESTNET  https://testnet.binancefuture.com =====
  BTCUSDT: first_open=2022-08-01T00:00:00Z open=23300.90  price_plausible=True
  ETHUSDT: first_open=2022-08-01T00:00:00Z open=1679.50   price_plausible=True

pagination continuity (MAINNET BTCUSDT 5m, 2022-08-01 +1000 bars):
  bars=1000  2022-08-01T00:00:00Z -> 2022-08-04T11:15:00Z  gaps=0
```

价格与 2022-08 真实水位吻合（BTC ≈ 23.3k、ETH ≈ 1.68k），非合成数据。

**历史边界 sweep**：

| startTime | BTCUSDT 5m | ETHUSDT 5m |
|---|---|---|
| 2019-09-01 | first_open `2019-09-08T17:55Z`（上市边界） | first_open `2019-11-27T07:45Z`（上市边界） |
| 2020-01-01 | exact match, open 7189.43 | exact match, open 129.12 |
| 2021-01-01 | exact match, open 28948.19 | exact match, open 737.18 |
| 2022-01-01 | exact match, open 46210.57 | exact match, open 3676.01 |
| **2022-08-01** | **exact match, open 23290.10** | **exact match, open 1677.63** |

15m / 1h / 4h 同样从 `2022-08-01T00:00:00Z` 可得。

```
P0-2 VERDICT: PASS
→ 48-month target 保持
→ I-3 AUTHORIZED
→ 5m 实际可回溯至 2019-09（BTC）/ 2019-11（ETH），48 月目标有余量
```

**pagination depth 仍需在 I-3 中确认**：本次只验证了首个 1000 根窗口连续，未验证跨 48 月全程分页无隐藏限流。

### P0-2 附带的边界问题（需你确认）

回补数据源取 **mainnet public klines**。这是只读 market data，不是执行，且仓库已有先例——`services/data/binance.py:163` 的 `_fetch_usdm_public_json()` 本身就先打 configured endpoint 再回落 legacy testnet base。但平台的执行不变量是 testnet-only，所以我不替你默认：

- **选项 1**：I-3 从 mainnet public klines 回补（数据质量权威，与执行链无关）
- **选项 2**：只从 testnet 回补（本次实测 testnet 也返回同样的真实历史，值极接近但非逐字节相同）

我倾向选项 1（研究数据应取权威源），但这需要你点头，因为它触及"testnet-only"的表述边界。

---

## 4. Root Cause Cards

### 4.1 T0-01 market_extras canonical symbol

| 项 | 内容 |
|---|---|
| 现象 | `DerivativesFeatures.funding_rate` / `open_interest` 恒为 None；`missing_features` 恒含 `funding_rate:missing`，并经 `RegimeScorerV2` 的 `missing_fraction` 抬高 `unstable` 分 |
| chain | `backfill_funding()` → `spot_to_usdm_perp_symbol(symbol)` → `normalize_funding_rate_history(symbol=…)` → `MarketExtras(symbol="BTC/USDT:USDT")` → `store_market_extras()` ⟶ **写入 legacy 形态**<br>`v2_scheduler_entry.py:116` `list_market_extras(symbol="BTC/USDT")` → `repository.py:322` `WHERE market_extras.c.symbol == symbol` ⟶ **精确等值，永远 0 行** |
| root cause | `spot_to_usdm_perp_symbol()`（`binance.py:141-146`）的输出被当作**持久化 identity**，而它只是 exchange-boundary identity。项目缺少统一的 `canonicalize_symbol()` 作为 DB 写入前的收敛点 |
| files | `services/data/binance.py:141,227,707-734`、`services/data/repository.py:57-67,260-300,314-330`、`services/data/universe.py:112-127` |
| current wrong behavior | DB 内 100% legacy 形态；所有 canonical 查询返回空 |
| target behavior | `market_extras.symbol` 只存 canonical；exchange 形态仅存在于 adapter 边界内部 |
| 连带风险 | 修完必须确认 `capabilities.py:33`、`market.py:91`、`market_intelligence.py:236`、`service.py:34`、`tasks.py:95-103` 这些构造 `:USDT` 的调用点，其产物是否会再次进入 DB 写路径 |

### 4.2 T0-02 历史覆盖与 5m

**42 月的来源（从实现反推，非估计）**：

`proposal_walk_forward.py:54-55` 硬校验 `window_count == 8 and train_months == 12 and oos_months == 3`：

```
12 (train) + 8 × 3 (OOS) = 36 月
+ data_manifest.json final_holdout_months: 6
= 42 月   ← 与 baseline required_total_months: 42 吻合
```

当前 12.7 月 ⇒ **缺口 ≈ 29.3 月**。采纳 D-01 的 48 月目标（起点约 2022-08），额外 6 月覆盖 EMA50 warm-up、缺失 bar、pivot confirmation lag、数据边界 buffer。

**5m 缺失的 root cause**：不是 fetcher 不支持。`binance.py:68-70` 的 `TIMEFRAME_TO_SECONDS` 含 `"5m": 5*60`；缺的是调度注册——`services/data/tasks.py:130` `_HEARTBEAT_TIMEFRAMES = ("1m","15m","1h","4h","1d")` **没有 5m**，`_HEARTBEAT_STALE_SECONDS` / `_TIMEFRAME_SECONDS` 同样缺。**这是配置缺口，不是能力缺口。**

**聚合决策（遵循 D-02，保持现有架构）**：现架构按 `(symbol, timeframe, timestamp)` 独立抓取与持久化。不改为"5m raw → 聚合出 15m/1h/4h"，因为那会让既有 37,134 根 15m 历史与新聚合结果在边界处不一致，并需重写 dedup。但**新增一条一致性测试**：5m 聚合出的 15m 必须与既有 15m 逐根一致（作为交叉校验，不替换数据源）。

### 4.3 T0-03 AI 归因（成因已更正）

> **对上一轮报告的更正**：我上轮说"`llm_invocations` 没有 bias 列 ⇒ AI attribution 无法进行"。前半句对，后半句错。

`services/agents/service.py:560-579` `_validate_advisory_review_output()` 严格校验 `bias ∈ {support, neutral, oppose}`、`confidence ∈ [0,1]`、`risk_flags: list[str]`，然后写入 `agent_tasks.output_payload["trade_review"]`。**bias 一直在落库**。

实测（`agent_tasks WHERE task_type='trade_review_llm'`，916 行）：

| task_status | schema_validation_status | 计数 |
|---|---|---|
| completed | passed | **188** |
| failed | failed | 209 |
| failed | provider_unavailable | 519 |

188 条 passed 的 bias 分布：

```
oppose  181  (96.3%)
support   6
neutral   1
```

**真实阻塞是三件，都不是 schema 缺 bias**：

1. **V2 侧零条成功** — 916 条 `input_ref` 全为 `paper_signal:...:ensemble`（legacy 前缀），无一条 `v2_candidate:` 前缀；passed 记录时间全部早于 V2 ACTIVE。
2. **join 断裂** — `agent_tasks` 无 `cycle_id` 列；`v2_execution_decisions.candidate_key` 13,367 行全 NULL。
3. **provider 可用率 56.7%** — 实测错误：OpenRouter nvidia/nemotron HTTP 429（493 次）、Azure `models.inference.ai.azure.com` 404（209 次）、WinError 10061 拒绝连接（21 次）。

**96.3% oppose 的含义**：若 `ai_advisory_veto` 接成硬闸门，会阻断几乎所有下单 ⇒ 操作员 `ai_advisory_veto = OFF` 的裁定在数据上成立。根因是 prompt 仅 6 字段（`ai_review_service.py:133-143`）上下文不足只能保守反对，但**修 prompt 属于 NEW S2，不在 T0**。

### 4.4 T0-04 manifest / runtime truth

| 项 | 内容 |
|---|---|
| 现象 | manifest 声明 `trend_momentum_v2_enriched`；V2 Active 实际执行 `testnet_sampling_v2`；`report_path: null` |
| root cause | manifest 是为 **legacy paper 链**设计的。唯一消费者是 `services/execution/bootstrap.py:153-174`（校验 `rules_hash`、`eligible_symbols`）。**V2 Active 从不读 manifest** ⇒ `manifest_eligible` 在 V2 侧没有赋值来源。这不是漏传参数，是 V2 没有 manifest 消费逻辑 |
| `report_path: null` | 反映从 v1 → v2_enriched 那次切换（理由"48h 零信号"）**没有经过任何 promotion 流程** |
| target | 让系统能准确表达四个互相独立的事实：谁 Active、其 role、谁领跑 research、谁有资格 promotion。**不把 `trend_momentum_v2_enriched` 接进 Active** |

### 4.5 T0-05 research gate observability

> **对上一轮报告的更正**：`net_edge_after_cost` 不需要从零设计。

legacy 已有完整实现：`decision_pipeline.py::_edge_stats_for_gate()` + `services/execution/signal_edge_stats.py`（gate on ≥30 真实 trade 样本，不足则回退 proxy）。计算形态为 `expected_return(win_rate, avg_win, avg_loss) - cost(fee+slippage 相对 stop_distance)`。

V2 的问题是**没接线**：`cycle_service.py:1528-1543` 构造 `EntryRuntimeContext` 时未传 `net_edge_after_cost_bps` / `manifest_eligible` / `ai_advisory_veto` / `risk_budget_available`，全部落默认值。`grep -rn "net_edge_after_cost_bps=" services/` 返回空。

缺口：**spread 未建模**（`baseline/cost_model.json` 明确标注 spread/latency/partial_fill 未建模）；funding 仅 `proposal_replay.py` 按结算事件计费，legacy replay 不计。

T0 只做**可观测**：在 Replay / Shadow 计算并记录，语义与 legacy 对齐或明确记录差异。`signal_edge_stats` 样本 <30 时必须标 `UNAVAILABLE`，**不得填 0 冒充真实值**。不接 Active hard gate。

### 4.6 T0-06 replay validity

已满足：

| 检查 | 证据 |
|---|---|
| point-in-time bar 边界 | `context.py:134` `bar.timestamp + delta <= decision_time` |
| closed-bar → next-bar-open 成交 | Phase 1 parity，replay 14 passed |
| 末根不造假成交 | 已实现 |
| purge + embargo 硬校验 | `proposal_walk_forward.py:54-60`（embargo ≥ 24h） |
| 确定性 hash | `canonical_hash` / `feature_snapshot_hash` |

**唯一待补**：`dow_trend._collect_pivots()` 循环范围 `range(pivot_window, len(frame) - pivot_window)` ⇒ 最新可确认 pivot 至少滞后 `pivot_window` 根。需显式暴露 `confirmation_lag_bars` 并补回归测试（RT-10 / I-8）。trendline 与 Fib 锚点尚未实现，一旦实现必须只用已确认 pivot。

---

## 5. File Map

### MUST_NOT_CHANGE

`cycle_service.py` 的 submit/fill/project/protection 段、`exit_service.py`、`entry_service.py` 的 drift / kill switch / quarantine、`runtime_lock.py`、`binance_adapter.py`、`reconciliation_service.py`、`protection_service.py`、`decision_service.py` 的 sampling 规则与 `DecisionContext` 硬编码、`services/execution/paper_*.py`（4 个 legacy 冻结）、`proposal_walk_forward.py` 的窗口硬校验、所有风险参数数值。

### MUST_CHANGE

| 文件 | 目标 | 任务 |
|---|---|---|
| `services/data/binance.py` | `MarketExtras` / `backfill_funding` 以 canonical 记账 | I-1 |
| `services/data/tasks.py` | `_HEARTBEAT_TIMEFRAMES` 等三处加 `5m` | I-2 |
| `services/strategy_library/models.py` | `LlmInvocation` 加 `agent_task_id` / `candidate_id` / `strategy_lane` / `prompt_version` | I-5 |
| `migrations/versions/0021_*` | 上述列的 additive 迁移（当前 head `0020`） | I-5 |

### MAY_CHANGE

`services/agents/service.py`（写 `LlmInvocation` 时补 spine 字段）、`services/data/repository.py`（迁移辅助）、manifest truth 字段、`services/strategy_library/technical/dow_trend.py`（暴露 `confirmation_lag_bars`）、`services/validation/proposal_replay.py`（记录 net_edge）。

### NEEDS_CODE_MAPPING

| 项 | 待确认 |
|---|---|
| provider 配置来源 | OpenRouter 429 / Azure 404 的配置点在 `build_configured_llm_runtime()`，需确认 key 与 endpoint 的实际配置文件（可能触及凭据，需按敏感文件处理） |
| 5m backfill 入口 | 复用 `BinanceBackfillService.backfill_ohlcv` 还是新脚本 |
| `candidate_id` / `strategy_lane` 可得性 | V2 侧 `input_payload` 已含 `candidate.candidate_id` / `lane`，legacy 侧未必 |

---

## 6. Schema / Data Migration Plan

### M-01 LlmInvocation attribution spine（additive，D-B′）

```
ALTER TABLE llm_invocations ADD COLUMN agent_task_id   VARCHAR(36) NULL;  -- 最重要
ALTER TABLE llm_invocations ADD COLUMN candidate_id    VARCHAR(120) NULL;
ALTER TABLE llm_invocations ADD COLUMN strategy_lane   VARCHAR(60)  NULL;
ALTER TABLE llm_invocations ADD COLUMN prompt_version  VARCHAR(40)  NULL;
CREATE INDEX ix_llm_invocations_agent_task_id ON llm_invocations (agent_task_id);
```

**不加 bias / confidence / risk_flags 列** —— 按 D-B′，`AgentTask.output_payload["trade_review"]` 是 AI RESULT SSOT，复制会造出第二份 truth。

join 路径：

```
V2 outcome (v2_managed_positions / v2_exchange_fills)
   ↑ cycle_id / decision_id
LlmInvocation                      ← ATTRIBUTION SPINE
   ↓ agent_task_id
AgentTask.output_payload["trade_review"]   ← AI RESULT SSOT
   → bias / confidence / risk_flags / summary
```

### M-02 market_extras canonical migration（D-A，事务式）

**执行顺序不可调换**：

```
1. I-1A READ-ONLY PRECHECK          (P0-1 已完成：SAFE_PLAIN_MIGRATION)
2. 暂停所有 DB writer                 (REQUIRED — 见 §12.3，需停 RuntimeScheduler/API，
                                       不只停 market_extras 那一路)
3. 数据库完整备份                     (REQUIRED，约 340 MB，须在停 writer 之后取)
4. collision audit（迁移时点重跑）      (REQUIRED，数据可能已变)
5. BEGIN IMMEDIATE
6. canonical migration
7. post-migration verification
8. COMMIT
9. 恢复 writer
```

collision 处理规则（不允许 Agent 自行发挥）：

| 情形 | 动作 |
|---|---|
| legacy 与 canonical 同 `(symbol, time)` 且 payload **完全等价** | 保留 canonical，删除 legacy duplicate |
| payload 任一业务字段不同 | `SYMBOL_MIGRATION_COLLISION` → **STOP**，不自动 merge，不执行整库 UPDATE |

本轮 P0-1 实测 canonical 0 行 ⇒ 走"纯 UPDATE"分支。**协议仍须在执行时完整走一遍。**

---

## 7. Red Tests（修改前必须稳定红灯）

| ID | 测试 | 预期失败原因 |
|---|---|---|
| RT-01 | 写入 exchange 形态 BTC → 查询 `BTC/USDT` 应返回 funding | 当前返回空 |
| RT-02 | 同 RT-01，ETH | 同上 |
| RT-03 | `MarketContextBuilder.build()` 后 `derivatives.funding_rate is not None` | 恒 None |
| RT-04 | `list_ohlcv_bars(symbol="BTC/USDT", timeframe="5m")` 非空 | bar_count = 0 |
| RT-05 | `build_proposal_walk_forward_windows()` 用真实数据构造 8 窗口 | 数据不足抛异常 |
| RT-06 | 5m 聚合出的 15m 与既有 15m 逐根一致 | 5m 不存在 |
| RT-07 | V2 cycle → `LlmInvocation.agent_task_id` → `AgentTask.trade_review.bias` 可确定 join | 列不存在 |
| RT-08 | manifest truth 四字段（active / role / research_leader / promotion）可读 | 字段不存在 |
| RT-09 | V2 侧存在 `input_ref` 以 `v2_candidate:` 起始且 `schema_validation_status='passed'` 的记录 | V2 侧零条 |
| RT-10 | swing 特征暴露 `confirmation_lag_bars >= pivot_window` | 字段不存在 |

> **RT-05 只能靠补数据转绿，不允许改 WFO 硬校验。** 改校验等于为了让实验跑起来降低验收标准。

---

## 8. Implementation Tasks（一个根因一个任务）

| ID | 任务 | 依赖 | 红灯 |
|---|---|---|---|
| I-1 | `market_extras` symbol canonical（写入侧 + M-02 事务迁移） | P0-1 PASS ✅ | RT-01/02/03 |
| I-2 | 注册 5m 到采集调度 | — | RT-04 |
| I-3 | 回补 BTC/ETH 5m/15m/1h/4h 至约 48 月 | I-2、P0-2 PASS ✅、数据源选项待定 | RT-05/06 |
| I-4 | 修 AI provider 可用性（429 / 404） | — | RT-09 |
| I-5 | `LlmInvocation` attribution spine（M-01）+ 写入侧补字段 | I-4 | RT-07 |
| I-6 | manifest / runtime truth 字段 | — | RT-08 |
| I-7 | research gate 可观测（只记录，不拦截） | I-1（funding 影响 cost） | — |
| I-8 | pivot `confirmation_lag_bars` 暴露 + 回归测试 | — | RT-10 |

### 8.1 操作员指定的执行顺序

```
P0-1 collision audit          ✅ PASS (SAFE_PLAIN_MIGRATION)
P0-2 5m availability probe     ✅ PASS (48-month target 保持, I-3 AUTHORIZED)
        ↓
第一组  I-1 Symbol Canonical    ← 先统一 symbol truth
        ↓
        I-2 5m Registration
        ↓
        I-3 48m Backfill
        ↓
AI 线（可与数据线并行；严格收口则等 I-1 完成再开）
        I-4 Provider  →  I-5 Attribution B′
        ↓
最后    I-6 manifest truth · I-7 gate observability · I-8 pivot lag
        ↓
        T0 FINAL ACCEPTANCE
```

**为什么 I-1 必须在 I-3 之前**：若先回补数百万条 5m，之后才发现 symbol 仍需迁移，等于主动放大 I-1 的爆炸半径。

---

## 9. Validation Matrix

| 任务 | 必须验证 |
|---|---|
| I-1 | RT-01/02/03 转绿；迁移后 `market_extras` 零 legacy 形态；备份存在且可回滚；writer 暂停与恢复有记录；`ohlcv_bars` 未被误改 |
| I-2 | RT-04 转绿；5m 与其他 timeframe 的 close semantics / timezone / timestamp identity 一致 |
| I-3 | RT-05/06 转绿；无 duplicate timestamp / missing bar / non-monotonic；`build_proposal_walk_forward_windows` 用真实数据成功构造**原设计 8 窗口**（未改硬校验） |
| I-4 | V2 侧出现 `schema_validation_status='passed'` 且 `input_ref` 以 `v2_candidate:` 起始的记录 |
| I-5 | RT-07 转绿；**zero ambiguous joins / zero many-to-many accidental joins / zero legacy-V2 namespace collision**；Active 下单结果不变 |
| I-6 | RT-08 转绿；`bootstrap.py` 读 manifest 不回归，`rules_hash` 校验仍有效 |
| I-7 | 记录出现在 funnel payload；`UNAVAILABLE ≠ 0`；**不新增任何拦截** |
| I-8 | RT-10 转绿；pivot 滞后有显式断言 |

每个任务交付需附：`ruff` / `mypy` / `pytest` 逐字末行 + `git diff --stat` + 基线对比（是否引入新失败）。

### 9.1 I-5 的验收标准（按 D-B′ 修正）

不是 `bias successfully copied to llm_invocations`，而是：

```
V2 cycle → LlmInvocation → AgentTask → trade_review.bias → realized outcome
100% 可确定 join
```

---

## 10. Rollback / Compatibility

| 任务 | 回滚 |
|---|---|
| I-1 | 反向 UPDATE + 代码 revert；**迁移前必须备份 331 MB DB**。按 D-A，`legacy reader fallback = FORBIDDEN` ⇒ 回滚靠备份，不靠双查兼容层 |
| I-2 | 纯增量，无兼容问题 |
| I-3 | dedup 保证重复回补安全 |
| I-4 | provider 配置恢复；fail-open 语义不变 |
| I-5 | additive 列，旧代码忽略即可；索引可 drop |
| I-6 | manifest 字段 additive |
| I-7 | 只记录，移除记录即回滚 |
| I-8 | 纯新增字段与测试 |

**唯一涉及 DB 写的是 I-1 的 M-02。** 必须先备份 `.local_paper_console.db`，操作员确认后才能执行。

---

## 11. T0 Completion Criteria

```
[ ] market_extras canonical query 返回真实 funding / OI
[ ] BTC/ETH 5m 存在且与其他 timeframe 语义一致
[ ] 历史覆盖满足原设计 WFO（未改硬校验）
[ ] V2 侧 AI review 成功执行并落库，且可确定 join 到 candidate/outcome
[ ] manifest 准确表达 runtime / research / promotion truth
[ ] net_edge_after_cost / manifest_eligible 语义已定义，无新增 Active 拦截
[ ] pivot / swing confirmation_lag_bars 显式暴露且有回归测试
[ ] 全量 ruff / mypy 无新增失败
[ ] testnet_sampling_v2 交易规则、Active 接线、风控数值均未改变
[ ] ai_advisory_veto 仍为 OFF；AI prompt/schema 未改
```

---

## 12. Final Status

```
T0_FROZEN_PLAN_READY

Authorization boundary : §0（操作员已冻结，实现阶段唯一边界）
P0-1                   : PASS  SAFE_PLAIN_MIGRATION（canonical 0 行，碰撞 0）
P0-2                   : PASS  48-month target 保持；I-3 AUTHORIZED
Next authorized action : 第一组 I-1 Symbol Canonical
Production             : UNCHANGED
Code changes this phase: NONE（仅新增本文件；P0 probe 脚本写在 /tmp，未入仓）
```

### 12.1 仍需你确认的一项

**I-3 回补数据源**：mainnet public klines（我倾向，研究数据取权威源，且 `_fetch_usdm_public_json` 已有先例）还是仅 testnet。这触及"testnet-only"表述边界，不替你默认。

### 12.2 本轮对上一轮报告的三处更正

| # | 上一轮说法 | 实际 |
|---|---|---|
| 1 | `llm_invocations` 无 bias ⇒ AI attribution 无法进行 | bias 一直落在 `agent_tasks.output_payload["trade_review"]`（188 条 passed 实测）。真实阻塞是 V2 侧零条 + join 断裂 + provider 56.7% |
| 2 | `net_edge_after_cost` 需从零设计 | legacy 已有完整实现（`decision_pipeline.py` + `signal_edge_stats.py`），V2 是没接线 |
| 3 | `universe.py` 的 `.replace(":USDT","")` 是"连带风险、需梳理防矛盾" | 方向说反。它们是 boundary normalizer 而非第二份 DB truth；验收应为"边界层幂等、数据层唯一" |

### 12.3 运行态实测补充（写回时发现）

写回本文件时复核发现**系统当前仍在运行**，这影响 I-1 的执行前提：

```
API 进程          PID 21472，LISTENING 127.0.0.1:8016
v2_execution_cycles  13,379 → 13,695（本轮观察期内仍在增长）
最新 cycle        2026-08-08 17:54:27  bar 17:45:00  CANDLE_CLOSED
最新 15m BTC bar  2026-08-09 00:00:00
DB 体积           331.3 MB → 340.6 MB，mtime = 当前
```

DB mtime 与体积变化来自**活跃 RuntimeScheduler writer**，与本轮只读 probe 无关
（probe 全程 `mode=ro`）。

但 `market_extras` 复核为 **n=106,292 未变**，时间窗仍为
`2026-07-26 05:12:45 → 08:32:20` ⇒ **market_extras writer 当前不活跃**
（funding/OI 采集早已停止，这本身也是 T0-01 的一个侧证）。

对 I-1 的含义：

| 项 | 结论 |
|---|---|
| writer pause 是否 load-bearing | **是** —— 虽然 `market_extras` writer 不活跃，但 scheduler 正在写同一个 DB 文件。M-02 的 `BEGIN IMMEDIATE` 会与 scheduler 的写事务争锁 |
| 建议 | I-1 执行窗口内**停止 RuntimeScheduler / API**，而非只停 market_extras 采集。否则事务可能 `SQLITE_BUSY`，或备份取到不一致快照 |
| 备份时机 | 必须在 scheduler 停止**之后**取，否则备份本身就是运行中快照 |

这一条修正了 §6 M-02 第 2 步"暂停 market_extras writer"的措辞——
实际需要暂停的是**所有 DB writer**，不只是 market_extras 那一路。

---

### 12.4 本文件的诚实边界

- 上一轮我在回复里描述过一份 T0 文档（"396 行 / 11 节"）与一批记忆文件，**那次写入实际未落盘**。本文件是首次真正创建。实质内容（阻塞矩阵、根因、红灯、任务）在上一轮已基于真实代码与 DB 核实。
- 本轮未跑 `ruff` / `mypy` / `pytest`（无代码改动）。
- `.env` 仍未读取（权限拒绝）；运行态由 DB 证据推断。
- P0 probe 脚本写在 `/tmp`，**未入仓**。若需可复现，应在 I-1 前把它们正式落到 `scripts/` 并加 SHA256 记录（参照 S1 的 executed-source 做法）。
- P0-2 只验证了首个 1000 根窗口连续，**未验证跨 48 月全程分页无隐藏限流**，此项留给 I-3。
