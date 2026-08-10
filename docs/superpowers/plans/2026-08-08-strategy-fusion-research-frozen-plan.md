# STRATEGY_RESEARCH_FROZEN_PLAN

> 阶段：**只读研究 + 方案设计 + 实验设计**。本文件不改任何生产代码。
> 生成时间：2026-08-08。证据来源：本仓库当前代码 + `.local_paper_console.db` 运行时数据 + `D:\douyin_research\整合稿_技术分析体系.md`。
> 所有"当前状态"结论均来自本次实际 Read / SQL 查询，不依赖 `当前状态.md` 等可能过期的文档。

---

## 1. 一句话结论

**当前策略系统最大的结构问题不是缺指标，而是"真正在交易所下单的策略"和"所有人以为在跑的策略"不是同一个东西**：

- `v2_active` 模式下真正决定开仓的是 `testnet_sampling_v2`，规则只有 **单周期 15m 的 EMA50 + MACD 柱 + RSI 区间** 三条（`services/automated_trading/application/decision_service.py:220-291`）。
- `trend_momentum_v2_enriched`（active manifest 里写的候选、带 4h EMA+ADX 趋势过滤 + 4 个 15m 入场信号）属于 **legacy paper 链**，在 `v2_active` 下 `allow_legacy_writer=False`，**完全不注册、不运行**。
- 三个 research 候选（`trend_pullback_v2` / `range_sweep_reversion_v1` / `failed_breakout_reversal_v1`）只在 `EngineActivation.SHADOW` 下产出 `research_shadow`，而 SHADOW 与 ACTIVE **互斥**。所以现在 Active 侧没有多周期结构，Research 侧没有任何对照数据。

**视频资料真正应该贡献的不是新指标，而是一套"职责分层"**：高周期定位置、低周期定时机、结构定止损、动量做确认、成交量做验证。这恰好是当前系统最缺的一层——`RegimeScorerV2` 把 15m 权重设为 0.50 而 4h 只有 0.20（`regime/scorer_v2.py:12-16`），与视频反复强调的"先看高周期"完全相反；`WeightedEnsembleService` 把趋势型和均值回归型信号放进同一个加权平均（`ensemble/weighted.py:103`），正是你担心的"指标投票汤"。

**推荐的下一代架构**：不新建第四套策略栈，而是把已经存在的 `MarketContext → RegimeScorerV2 → 三候选 → CandidateSelectorV2` 这条 research 流水线**收敛为唯一策略主干**，让它能与 ACTIVE 并行产出 Shadow 数据，然后把 `trend_pullback_v2` 作为第一个走完晋级通道的候选。

**三条必须先说清的硬约束**（详见第 15 节）：

1. **5m 数据完全缺失**（BTC/ETH `5m` bar_count = 0）。Gatsby 的"5m 反转触发"当前**无法研究**，必须先回补历史。
2. **可用历史只有约 12.7 个月**（BTC/ETH 15m：2025-07-17 → 2026-08-08）。现有 `build_proposal_walk_forward_windows` 硬编码要求 8×3 个月 OOS + 12 个月训练（+6 个月 holdout ≈ 42 个月），**当前数据量无法满足，会直接抛异常**。
3. **只有 1 笔已平仓 V2 交易**（ETH long 1912.52 → 1919.23）。任何"策略好坏"的结论在当前实盘样本上都不成立，必须走 replay。

---

## 2. 当前策略系统地图（真实调用链）

### 2.1 执行链（`v2_active`）

```
RuntimeScheduler.start()                       services/execution/scheduler.py:177
  └─ resolve_engine_activation(settings)       automated_trading/infrastructure/runtime_lock.py:44
       engine_flag = settings.automated_trading_engine   (默认 "legacy"，shared/config.py:52)
       v2_active → EngineActivation.ACTIVE, allow_legacy_writer=False
  └─ resolve_scheduler_v2_jobs()               services/execution/v2_scheduler_entry.py:52
       ACTIVE  → {"automated_trading_v2_cycle"}                 ← 只有这一个
       legacy  → {"paper_runtime_cycle", "paper_observation_cycle"}
       v2_shadow → 三个都有
  └─ _default_v2_automated_trading_runner      scheduler.py:210-219
       └─ execute_v2_automated_trading_cycles  v2_scheduler_entry.py:340+
            ├─ load_timeframe(symbol, "15m")   → TimeframeView (limit=200 bars)
            ├─ if activation is SHADOW:  _research_shadow_payload(...)   ← ACTIVE 下不执行
            └─ run_automated_trading_cycle()   automated_trading/application/cycle_service.py:1208
                 ├─ fetch_authoritative_snapshot()    (Binance Testnet 真源)
                 ├─ reconcile / recovery / protection projection
                 ├─ evaluate_symbol(DecisionContext)  decision_service.py:337
                 │    lane        = CandidateLane.TESTNET_SAMPLING   ← 硬编码 cycle_service.py:1452
                 │    strategy_id = "testnet_sampling_v2"            ← 硬编码 cycle_service.py:1453
                 │    └─ evaluate_sampling_signal()   decision_service.py:220
                 ├─ evaluate_entry(candidate, EntryRuntimeContext, None)   风控闸门
                 ├─ _run_trade_review_budgeted()      AI TRADE_REVIEW（advisory）
                 ├─ evaluate_entry(..., snapshot_market)                   价格漂移闸门
                 └─ submit → fill → project → protection
```

### 2.2 真正的 Active 入场规则（唯一在交易所下单的规则）

`decision_service.py:220-296`，全部基于**单一 15m 周期**：

| 项 | 规则 |
|---|---|
| LONG | `close > EMA50` 且 `MACD_hist > 0` 且 `50 ≤ RSI14 ≤ 72` 且 `ATR14 > 0` |
| SHORT | `close < EMA50` 且 `MACD_hist < 0` 且 `28 ≤ RSI14 ≤ 50` 且 `ATR14 > 0` |
| confidence | 恒定 `0.55`（写死，不随信号强度变化） |
| stop | `max(1.2 × ATR14, price × 0.0035)` |
| take profit | `1.5 × stop_distance`（**注意：不是 registry 里写的 2.0R**，`decision_service.py:473`） |

**没有** 4h 方向过滤、**没有** 1h regime、**没有** 结构判断、**没有** 成交量确认、**没有** 关键位。

### 2.3 三层策略栈并存且互斥

| 层 | 入口 | 候选 | 何时运行 | 是否下单 |
|---|---|---|---|---|
| A. V2 Active | `cycle_service.py:1449` | `testnet_sampling_v2`（硬编码） | `v2_active` | ✅ 真实 Testnet |
| B. Legacy Paper | `paper_cycle_orchestrator.py` | `trend_momentum_v2_enriched`（manifest） | `legacy` / `v2_shadow` | 冻结链路 |
| C. Research Shadow | `proposal_pipeline.py:55` | `trend_pullback_v2`、`range_sweep_reversion_v1`、`failed_breakout_reversal_v1` | **仅 SHADOW** | ❌ 只写 payload |

**这是本次研究要解决的第一个结构问题**：A 与 C 互斥 ⇒ 无法同时拿到"真实执行"和"研究对照"。

### 2.4 Active manifest 现状

`docs/evidence/active-manifests/auto_paper_mature_templates.json`：

```json
{"candidate_id": "trend_momentum_v2_enriched",
 "eligible_symbols": ["BTC/USDT", "ETH/USDT"],
 "report_path": null,                                    ← 无 OOS 证据附件
 "change_rationale": "2026-08-07: switch from trend_momentum_v1 ... to address signal scarcity"}
```

manifest 指向的候选与 V2 Active 实际执行的 `testnet_sampling_v2` **不是同一个策略**。`report_path: null` 说明这次切换没有附任何回测证据——切换理由是"v1 在 48h 内零信号"，属于**信号密度驱动**而非**期望值驱动**。

### 2.5 V2 Active 中三个闸门结构性失效

`cycle_service.py:1528-1543` 构造 `EntryRuntimeContext` 时**没有传入**以下字段，全部落到 dataclass 默认值：

| 字段 | 默认值 | 后果 |
|---|---|---|
| `net_edge_after_cost_bps` | `None` | `entry_service.py:167` 的 `is not None` 判断永假 → **净边缘闸门从不触发** |
| `manifest_eligible` | `True` | `entry_service.py:165` 永不拦截 → **manifest 闸门在 V2 Active 无效** |
| `ai_advisory_veto` | `False` | `entry_service.py:178` 永不触发 → **AI 无论 OPPOSE 都不影响下单** |
| `risk_budget_available` | `True` | 永不拦截 |

（`grep -rn "net_edge_after_cost_bps=" services/` 返回空，确认全仓无任何赋值点。）

> 这一条不属于本阶段可修改范围（涉及风控闸门语义），已标记为待操作员决策项，见第 20 节 R-01。

---

## 3. 现有策略能力矩阵（CURRENT_SYSTEM_CAPABILITY_MATRIX）

Active 一列指的是"在 `v2_active` 下真实影响交易所下单"。

| 能力 | 当前实现 | Active? | 时间框架 | 实际作用 |
|---|---|---|---|---|
| 趋势识别 | `evaluate_sampling_signal` close vs EMA50 | ✅ | 15m 单周期 | 唯一的方向来源 |
| HH/HL/LH/LL | `context.py:173 _structure()` 计数 | ❌ | 15m 近 48 根 | **相邻 bar 逐根比较计数**，不是 swing 结构；仅存在于 MarketContext |
| Dow 结构 | `technical/dow_trend.py` | ❌ legacy | 任意 | `pivot_window=2` → 5 根窗口微观 swing；非 HH/HL 时退化为斜率均值 |
| EMA | `indicators.py:54` spread+slope；`decision_service.py:236` EMA50 | ✅（仅 EMA50） | 15m | Active 只用 close/EMA50 布尔比较 |
| MACD | `decision_service.py:178` 柱值；`technical/macd.py` 交叉 | ✅（仅柱符号） | 15m | legacy 版**回溯 6 根找交叉**，找不到才用柱 → 90 分钟前的旧交叉仍投票 |
| Bollinger | `indicators.py:165` 回band内反转 | ❌ legacy | 15m | **语义是均值回归**，却被 enriched 候选当趋势入场信号加权 |
| Volume | `context.py:191 _volume()` ratio；`trend_pullback_v2:147` 缩量条件 | ❌ | 15m | Active 完全不看成交量 |
| ATR | `decision_service.py:202` | ✅ | 15m | 止损距离 + 有效性检查 |
| 支撑阻力 | `price_action.py:69` Donchian-20；`failed_breakout_reversal_v1` Donchian-24 | ❌ | 15m | 无 4h 关键位、无 touch_count、无 level_strength |
| Pullback | `trend_pullback_v2.py:151-168` | ❌ SHADOW | 15m | 已实现 EMA 回踩 + 缩量 + 次根确认 |
| Breakout | `price_action.py:69` | ❌ legacy | 15m | 仅收盘破 Donchian，无动能/放量校验 |
| Failed Breakout | `failed_breakout_reversal_v1.py` | ❌ SHADOW | 15m | Donchian-24 扫破后收回 + 次根确认 |
| Range | `range_sweep_reversion_v1.py` | ❌ SHADOW | 15m | 流动性扫单回归 |
| Regime | `RegimeScorerV2` | ❌ SHADOW | 15m/1h/4h | **权重 15m=0.50 / 1h=0.30 / 4h=0.20**；direction 仅为窗口总收益 ÷ 3% |
| Stop Geometry | `decision_service.py:294` ATR/固定；`trend_pullback_v2:63` 结构止损 | ✅ ATR / ❌ 结构 | 15m | Active 是纯 ATR，**不锚定结构** |
| Partial TP | `AdaptiveExitPlan.targets` (0.35/0.40/0.25) | ❌ | — | 仅 `proposal_replay.py:380` 调用 |
| Trailing Exit | `AdaptiveExitPlan.trailing_activation_r=1.8` | ❌ | — | 同上，research only |
| Funding | `DerivativesFeatures.funding_rate` | ❌ | — | **数据层断裂**，见 3.1 |
| OI | `DerivativesFeatures.open_interest` | ❌ | — | 同上 |
| Long/Short | `long_ratio` / `short_ratio` | ❌ | — | 同上 |
| Liquidation | `liquidation_usd` | ❌ | — | 同上 |
| News/Macro | `news_items` / `macro_events` 表 | ❌ | — | 表存在，未接入决策 |
| AI Review | `ai_review_service.py` | ⚠️ 调用但无权限 | — | prompt 只含价格/方向/置信度/止损距离；`advisory_veto` 未接线 |

### 3.1 已确认的数据层缺陷（衍生品特征恒为 missing）

两个独立原因叠加：

1. **数据量**：`market_extras` 仅覆盖 `2026-07-26 05:12` → `08:32`，约 **3.3 小时**。
2. **符号命名空间不匹配**：`market_extras.symbol` 存的是 `BTC/USDT:USDT`，而 `v2_scheduler_entry.py:116` 用 `symbol="BTC/USDT"` 查询，`repository.py:322` 是精确等值匹配 `market_extras.c.symbol == symbol` ⇒ **永远返回空**。

```
DB 实测：
  market_extras distinct symbol → ['ADA/USDT:USDT', ..., 'BTC/USDT:USDT', 'ETH/USDT:USDT', ...]
  v2_execution_cycles symbol    → [('BTC/USDT', 6696), ('ETH/USDT', 6684)]
```

因此 `funding_rate:missing` / `open_interest:missing` 恒进 `missing_features`，并通过 `RegimeScorerV2` 的 `missing_fraction` 抬高 `unstable` 分。**任何基于 funding/OI 的研究在修掉这个映射前都不可能有信号。**

### 3.2 真实决策漏斗（13,379 个 cycle，实测）

```sql
SELECT terminal_reason, COUNT(*) FROM v2_execution_decisions GROUP BY 1 ORDER BY 2 DESC;
```

| terminal_reason | 计数 | 解读 |
|---|---|---|
| `DUPLICATE_DECISION` | 12,396 | 调度频率 > 15m bar 周期，属正常去重空转 |
| `CANDIDATE_READY` | **259** | 真正生成候选 |
| `MACD_DIRECTION_MISMATCH` | 256 | 信号闸门 |
| `RSI_OUTSIDE_RANGE` | 160 | 信号闸门 |
| `UNMANAGED_EXTERNAL_POSITION` | **110** | ← 手动基线阻挡 |
| `NO_ENTRY_SIGNAL` | 54 | 信号闸门 |
| `RECONCILIATION_DEGRADED` | 32 | 对账降级 |
| `MARKET_DATA_STALE` | 31 | 数据新鲜度 |
| `POSITION_ALREADY_OPEN` | 14 | 单符号单仓 |
| `PRICE_DRIFT_EXCEEDED` | 5 | 漂移闸门 |
| `SHADOW_MODE_NO_SUBMIT` | 4 | 历史 shadow 残留 |
| `OK` | 2 | 成交 |

**关键结论：真实瓶颈不在信号质量。** 排除去重后约 983 次有效评估中，259 次（26%）已生成候选；候选之后被 `UNMANAGED_EXTERNAL_POSITION`(110) + `POSITION_ALREADY_OPEN`(14) + `RECONCILIATION_DEGRADED`(32) 拦掉的量级，与信号闸门拦掉的量级相当。

> **这直接影响实验设计**：在手动基线（`BTC short 0.5302` / `ETH short 6.814`）存在期间，即使把入场信号质量提高一倍，Testnet 成交数也不会明显上升。因此 **Entry 质量研究必须走 replay，不能靠 Testnet 观察出结论**。

### 3.3 真实成交样本（实测）

| 来源 | 数量 |
|---|---|
| `v2_managed_positions` CLOSED | **1**（ETH long 0.185 @1912.52 → 平 @1919.23，realized_pnl `0.9578`） |
| `v2_managed_positions` PROTECTED | 1（BTC long 0.0388 @64996.2，未平） |
| `v2_exchange_fills` | 3（1 entry + 1 reduce-only exit + 1 entry） |
| legacy `position_records` CLOSED | 38（含 `PAPER_SIMULATION_ONLY` 130、`RECONCILED_GHOST` 51，来源混杂不可用于策略归因） |

**1 笔已平仓交易 ⇒ 任何胜率/期望值/PF 都无统计意义。**

### 3.4 AI 调用实况（实测 6,173 条）

| stage | status | 计数 |
|---|---|---|
| TRADE_REVIEW | skipped | 4,138 |
| TRADE_REVIEW | provider_unavailable | 1,091 |
| TRADE_REVIEW | failed | 395 |
| TRADE_REVIEW | passed | **383** |
| MARKET_REVIEW | provider_unavailable | 97 |
| MARKET_REVIEW | failed | 37 |
| MARKET_REVIEW | passed | 32 |

AI **确实在被调用**（不再是历史上的"用量为 0"），但：

- 真实调用中 `failed`(395) ≈ `passed`(383)，成功率约 49%。
- **`llm_invocations` 表没有 `bias` / `confidence` / `risk_flags` 列**（`strategy_library/models.py:747-767`）。`AIReviewResult` 里解析出的 bias 只存在于内存，落库时被丢弃。
- 因此 **SUPPORT / NEUTRAL / OPPOSE 对照研究在当前 schema 下无法进行**，这是 T7 的前置阻塞项。

---

## 4. 视频思想 → 当前项目映射矩阵（SOURCE_TO_PROJECT_MAPPING）

Decision 取值：`KEEP` / `REFINE` / `MERGE` / `RESEARCH_ONLY` / `REJECT`。

| SOURCE_ID | 来源思想 | 可程序化的核心 | 主观/不可重复部分 | 项目现有对应 | 重复? | 冲突? | 建议动作 | 验证方式 |
|---|---|---|---|---|---|---|---|---|
| DOW-01 | 三级趋势（主/次/短波） | 多周期趋势层级 | "噪音"边界 | ❌ 无层级；`RegimeScorerV2` 三周期加权成一个分 | 否 | **是**：15m 权重 0.50 > 4h 0.20 | **REFINE** | 权重反转 A/B |
| DOW-02 | HH/HL/LH/LL 定义趋势 | swing 结构状态机 | — | `_structure()` 逐根计数（非 swing）；`dow_trend.py` 5 根窗口 | 部分 | 是：两种实现语义不同 | **REFINE** → 统一 `market_structure_state` | 结构标签稳定性 |
| DOW-03 | 成交量确认趋势；回调应缩量 | `pullback_volume_ratio` | — | `trend_pullback_v2:149` 已有缩量条件 | 是 | 否 | **KEEP + 消融** | T6 消融 |
| DOW-04 | 趋势持续至明确反转信号 | 结构失效才反手 | "明确"的主观性 | `close_on_opposite_signal: True`（legacy） | 否 | 是：对手信号≠结构失效 | **REFINE** | Exit 实验 |
| DOW-05 | 工业/运输指数背离 | 跨品种确认 | 加密无对应指数 | 无 | — | — | **REJECT**（标的不适用） | — |
| DOW-06 | 价格快速反映新闻 | 事件窗口降风险 | — | `news_items`/`macro_events` 表存在未接入 | 否 | 否 | **RESEARCH_ONLY** | 后续独立立项 |
| WAVE-01 | 推动浪/调整浪 | impulse / pullback 量化 | 浪的边界 | `trend_pullback_v2` impulse+pullback | 是 | 否 | **MERGE 进 pullback** | T2 |
| WAVE-02 | 自动数 1/2/3/4/5/A/B/C | — | **标签不稳定、可重画、回测不可复现** | 无 | — | — | **REJECT** | 见第 11 节 |
| WAVE-03 | 第三浪最长/第二浪不破起点/第四浪不重叠 | 可写成校验式 | 依赖浪标签 | 无 | — | — | **REJECT**（依赖 WAVE-02） | — |
| WAVE-04 | 浪与 Fib 比率对应（2浪50/61.8，3浪161.8…） | retracement ratio | 依赖浪标签 | 无 | — | — | **RESEARCH_ONLY**（只取 ratio，不取浪号） | T4 |
| GANN-01 | 时间比价格更重要 | 时间类 feature | — | `SessionFeatures(utc_hour, utc_weekday)` 已有 | 部分 | 否 | **RESEARCH_ONLY** | T? 低优先 |
| GANN-02 | 45/90 天、周年、节气窗口 | — | **无因果、参数空间巨大、极易过拟合** | 无 | — | — | **REJECT** 作为 hard rule | — |
| GANN-03 | 角度线/九方图/轮中轮 | — | 需人工选锚点，不可复现 | 无 | — | — | **REJECT** | — |
| GANN-04 | 永远设止损/不加仓亏损/不过度交易 | 硬风控 | — | 已实现（无止损拒绝执行、禁 Martingale） | 是 | 否 | **KEEP**（已满足） | 现有测试 |
| TL-01 | 趋势线阻挡→反转 / 突破→延续 | 关键位二元结果 | 画线锚点主观 | 无趋势线；有 Donchian | 否 | 否 | **RESEARCH_ONLY** | T3 |
| TL-02 | **假突破=细小疲软 K；真突破=大幅 K** | `body/ATR`、`penetration/ATR`、`close_location`、`volume_ratio`、`follow_through` | — | `false_breakout` 仅用 `wick ≥ 1.5×body`，**无 ATR 归一、无放量校验** | 部分 | 否 | **REFINE**（最高价值项之一） | T3 消融 |
| TL-03 | 等结构破位后回撤入场 | 破位 + 回抽确认 | — | `trend_pullback_v2` 次根确认 | 部分 | 否 | **MERGE** | T2 |
| TL-04 | 止损设在左侧近期高/低点外 | 结构止损 | — | ❌ Active 是纯 ATR | 否 | **是** | **REFINE** | Exit 实验 |
| FIB-01 | **强趋势看 0.382 最强** | `retracement_ratio` 连续特征 | "最强"是断言，无统计证据 | 无 | 否 | 否 | **RESEARCH_ONLY**，只作 feature | T4 分档 expectancy |
| FIB-02 | 0.5 以下=深回调 | 同上 | — | 无 | 否 | 否 | **RESEARCH_ONLY** | T4 |
| FIB-03 | 三根 K 定 swing 锚点 | pivot 定义 | — | `dow_trend._collect_pivots` 5 根 | 部分 | 否 | **REFINE** 统一 pivot 定义 | T1 |
| FIB-04 | Fib 共振区（clusters） | 多水平重叠计数 | — | 无 | 否 | 否 | **RESEARCH_ONLY** | T4 后续 |
| EMA-01 | 50EMA 判断环境不是买卖信号 | trend bias | — | Active **正把 close/EMA50 当买卖信号** | 否 | **是** | **REFINE** | T2 |
| EMA-02 | 只在回踩均线时入场 | pullback proximity | — | `trend_pullback_v2:154` `pullback.low ≤ fast_ema` | 是 | 否 | **KEEP** | T2 |
| EMA-03 | 价格不必完美触线，接近即可 | `distance/ATR` 容差 | — | 已有 `maximum_entry_distance_atr=3` | 是 | 否 | **KEEP** | — |
| EMA-04 | 需与其他信号组合 | 多维确认 | — | 有组合但为同质加权 | 部分 | 是 | **REFINE**（见第 18 节） | — |
| MACD-01 | **先看高周期结构再看交叉** | 分层前置 | — | Active 无高周期；legacy 回溯 6 根 | 否 | **是** | **REFINE** | T5 |
| MACD-02 | 柱体扩张=动量增强 | `hist_slope`、`hist_accel` | — | 只有 `hist` 符号 | 否 | 否 | **REFINE** | T5 |
| MACD-03 | 金叉后仍需等新高确认 | `cross_age` + 结构确认 | — | 无 `cross_age` | 否 | 是 | **REFINE** | T5 |
| MACD-04 | 背离 | `divergence` | 背离画法有歧义 | 无 | 否 | 否 | **RESEARCH_ONLY** | T5 后续 |
| BB-01 | **不能单独用作超买超卖（=逆势）** | — | — | `bollinger` 均值回归信号**被 enriched 当趋势入场用** | 否 | **是（最严重语义冲突）** | **REFINE**：转 regime/volatility feature | T? |
| BB-02 | 收窄=盘整，扩张=趋势 | `BBW`、`BBW percentile`、squeeze | — | `RegimeScorerV2` 用 TrueRange 比率近似 | 部分 | 否 | **MERGE 进 regime** | Regime 实验 |
| BB-03 | OBV 背离提示突破方向 | `OBV slope`、`OBV divergence` | — | **无 OBV** | 否 | 否 | **RESEARCH_ONLY** | T6 |
| GAT-01 | **大周期卡关键位，小周期找时机** | 职责分层 | — | 无（Active 单周期） | 否 | **是** | **REFINE**（本方案核心） | T2/T3 |
| GAT-02 | 4H 找关键支撑阻力位 | `key_level` 特征组 | 人工挑位 | 无 4h 关键位 | 否 | 否 | **RESEARCH_ONLY** → T3 | T3 |
| GAT-03 | 5m 裸 K 反转触发 | engulfing/pin/reclaim | "最佳时机"主观 | `price_action.py` 有形态但用于 15m | 部分 | 否 | **RESEARCH_ONLY**，**5m 数据缺失阻塞** | T3（阻塞） |
| GAT-04 | 止损放反转 K 外侧 | 结构止损 | — | 无 | 否 | 是 | **REFINE** | Exit 实验 |
| GAT-05 | +3R 移动止损到保本 | breakeven 规则 | "3R 最好"无证据 | `trailing_activation_r=1.8`（research） | 部分 | 否 | **RESEARCH_ONLY**，作为 Exit 实验组之一 | Exit 实验 |
| GAT-06 | **未有效突破就反复进场** | retry 计数 | 与 5% 风险档叠加=连续大损 | 无 | 否 | **是（高危）** | **RESEARCH_ONLY**，默认 `max_retries=0` | 见第 17.4 |
| GAT-07 | 有效突破=5m 大 K 彻底贯穿→放弃 | `penetration/ATR` + `body/ATR` | — | 无 | 否 | 否 | **RESEARCH_ONLY** | T3 |
| GAT-08 | 只做反转不做突破（R:R 更优） | 断言 | 需自证 | 三候选中 2 个是反转型 | 部分 | 否 | **RESEARCH_ONLY**（用自己数据验证） | T3 vs T2 对比 |
| COMMON-01 | 先看高周期再决定 | 分层职责 | — | 冲突（见 DOW-01） | — | 是 | **REFINE** | Regime 实验 |
| COMMON-02 | 单一指标不可用，要共振 | 独立维度评分 | — | 有加权但同质 | 部分 | 是 | **REFINE**（第 18 节） | 相关性分析 |
| COMMON-03 | 用 K 线动能区分真假 | `body/ATR` 等 | — | 仅 `wick/body` | 部分 | 否 | **REFINE** | T3 |
| COMMON-04 | 等确认不抢先 | 次根确认 | — | 三候选均有次根确认 | 是 | 否 | **KEEP** | — |
| COMMON-05 | 止损由结构决定不由金额决定 | 结构止损 | — | Active 纯 ATR | 否 | **是** | **REFINE** | Exit 实验 |

---

## 5. 推荐保留能力（KEEP）

**不要再碰的基础设施**（本轮研究的保护契约对象）：

1. **整条 exchange-first 执行链**：`cycle_service.py` 的 snapshot → reconcile → intent → submit → fill → project → protection → reduce-only exit。1 笔完整闭环已在真实 Testnet 验证（fill `16754353826` / exit `16754436749`）。
2. **单写入者机制**：`runtime_lock.py` + fencing token + `check_fencing_conflict`。
3. **真实保护单**：`v2_protection_records` + `PROTECTION_FILLED` 路径。
4. **价格漂移闸门**：`drift_ceiling_bps` 的"候选自带容差更严时不被 ATR 放宽"逻辑（`entry_service.py:94-100`）。
5. **决策漏斗记录**：`decision_funnel.py` + `v2_execution_decisions`，这是本次能拿到真实瓶颈分布的唯一原因。
6. **手动仓位隔离**：`UNMANAGED_EXTERNAL_POSITION` + 反向候选 fail-closed。**必须保留**（用户明确要求手动仓位不平仓）。

**策略侧可直接复用、不需重写**：

7. `MarketContext` / `MarketContextBuilder`：point-in-time 语义正确（`_closed_window` 用 `bar.timestamp + delta <= decision_time` 严格排除未收盘 bar），runtime 与 replay 共用同一构造器。**这是防泄漏的地基，保留。**
8. `trend_pullback_v2` 的核心骨架：EMA 回踩 + 缩量 + 次根确认 + 结构止损 + cost-adjusted R:R 预筛（`cost_adjusted_rr <= 0` 直接返回 None）。**这已经是视频 EMA-02/TL-03/COMMON-04/COMMON-05 的正确实现。**
9. `CandidateSelectorV2` 的对向冲突拒绝（`conflict_margin=0.10`）。比加权平均正确：**方向冲突时拒绝交易，而不是投票平均出一个方向**。
10. `AdaptiveExitPlan`：按真实成交价重算 partial TP 的逻辑（`adaptive_exit.py:51-58`）已正确处理"提案价 ≠ 成交价"。
11. `build_proposal_walk_forward_windows`：purge + embargo 已实现且强校验。
12. 三候选的 `feature_snapshot_hash` / `canonical_hash`：replay 可复现性的基础。
13. AI 的 **fail-open** 设计：provider 失败绝不阻塞确定性执行（`cycle_service.py:1582-1590`）。在 provider 不可用率 1,091/6,173 的现实下，这个设计救了执行链。

---

## 6. 推荐重构/升级能力（REFINE / MERGE）

按优先级排序。**均为研究设计，不在本阶段实施。**

### R-1（最高）打通 Shadow 与 Active 并行

**问题**：`v2_scheduler_entry.py:361` `if config.v2_activation is EngineActivation.SHADOW` ⇒ ACTIVE 下 research_shadow 恒为 None。
**目标**：ACTIVE 下也计算 research_shadow 并写入 funnel payload，但**绝不参与下单**。
**为什么**：没有它，第 13/14 节的所有实验都拿不到与真实执行同期的对照数据。
**风险**：`run_proposal_pipeline` 会增加每 cycle 延迟。必须先测量，且必须在 `pretrade_max_decision_age_seconds=75` 预算内。

### R-2 Regime 时间框架权重反转

**问题**：`DIRECTION_WEIGHTS = {15m: 0.50, 1h: 0.30, 4h: 0.20}` 与 COMMON-01 相反。
**目标**：作为实验参数而非直接改。至少测三组：现状 / 均权 / 反转（4h:0.50, 1h:0.30, 15m:0.20）。
**注意**：`_direction()` 目前是"窗口首尾收益 ÷ 3%"，80 根 4h ≈ 13 天的总收益——这不是趋势方向，是区间涨跌幅。需一并研究是否替换为 EMA 排列 + 斜率。

### R-3 指标去重：把方向投票器降级为 feature

| 当前 | 目标角色 |
|---|---|
| `bollinger` 均值回归投票 | volatility/regime feature（BBW、squeeze） |
| `macd` 方向投票（含 6 根回溯） | momentum feature（`hist_slope`、`cross_age`） |
| `ema_trend` + `dow_trend` + `mtf_ma` 三票 | 合并为单一 `trend_bias`（三者高度相关） |
| `rsi` 均值回归投票 | 保留为极值过滤，不做方向 |

**依据**：`family/selector.py:26-41` 把 10 个信号映射到 4 个 family，但 `weighted.py:103` 最终仍是全体加权平均——family 分类没有阻止跨语义相加。

### R-4 统一 pivot / 结构定义

现有三套互不相同：`_structure()` 逐根计数、`dow_trend` 5 根窗口、`price_action` Donchian-20、`failed_breakout` Donchian-24。
**目标**：单一 `market_structure_state` 契约（字段见第 8 节），全部候选共用。

### R-5 结构止损

Active 现为纯 ATR（`max(1.2×ATR, 0.35%)`）。`trend_pullback_v2` 已有结构止损（`pullback.low - atr×0.25`）。
**目标**：作为 Exit 实验组对比，**不在 Entry 实验期间同时改**。

### R-6 AI 输入/输出契约

- 输入：当前 prompt 仅 6 个字段（`_build_trade_prompt:133-143`）。目标见第 12 节。
- 输出：`llm_invocations` 需要 `bias` / `confidence` / `risk_flags` / `reason_codes` 列，否则无法做归因。**这是 schema 变更，需操作员批准（第 20 节 R-03）。**

### R-7 修复 `market_extras` 符号映射

见 3.1。**在修复前，禁止立项任何 funding / OI / long-short / liquidation 研究**——否则会得到"这些特征无用"的假结论。

---

## 7. 明确拒绝进入生产的内容（REJECT / RESEARCH_ONLY）

### 7.1 REJECT（不进入生产设计）

| 项 | 拒绝理由 |
|---|---|
| 自动数 Elliott Wave（1/2/3/4/5/A/B/C） | 标签随新 bar 到来会重画（repainting）；同一段行情不同起点得到不同浪号；回测不可复现；`WAVE-03` 的三条黄金法则依赖浪标签，连带失效 |
| 江恩 45/90 天、周年、节气、角度线、九方图、轮中轮 | 无因果机制；参数/锚点空间极大；锚点需人工挑选 ⇒ 不可复现；在 12.7 个月数据上做周期检验必然过拟合 |
| 固定 Fibonacci magic number 作为 hard gate | "0.382 最强"是视频断言，无本标的统计证据。只允许作连续 feature |
| 裸 MACD cross 直接入场 | 违反 MACD-01；且当前 legacy 实现回溯 6 根，等于用 90 分钟前的旧交叉开单 |
| 裸 Bollinger 超买超卖 | 视频自己明确警告等于逆势交易 |
| AI 直接下单 / 创造候选 | 违反项目硬约束（AI 不决定订单数量、入场价、止损价） |
| 无限 retry | 见 7.2 GAT-06 |
| 指标越多越好 | `trend_momentum_v2_enriched` 的历史正是"从 1 个入场信号加到 4 个"，理由是信号稀缺而非期望值 |
| 工业/运输指数背离 | 加密标的无对应指数 |

### 7.2 RESEARCH_ONLY（可研究，不得直接进生产）

| 项 | 边界条件 |
|---|---|
| Fibonacci retracement ratio | 仅作 feature，分档统计 expectancy；无 OOS 证据前不得成为 hard gate |
| 时间类 feature（session/hour/weekday/bars_since） | 只用已有 `SessionFeatures`；不引入江恩周期 |
| MACD divergence | 需先定义无歧义的背离算法并证明无未来函数 |
| OBV | 先做 research feature，经消融证明边际价值 |
| Gatsby 关键位反转（`key_level_reversal_v1`） | **5m 数据缺失，当前无法立项**；需先回补历史 |
| Gatsby +3R 保本 | 仅作 Exit 实验组之一，与现有 fixed / partial / trailing 并列对比 |
| **Gatsby retry（GAT-06）** | **默认 `max_retries=0`**。理由：当前 `risk_per_trade=0.05`，第 2/3 次进场叠加后单一关键位可累积 10-15% 账户风险。必须先独立证明 attempt-2 / attempt-3 各自有正 expectancy 才允许 >0，且必须同时定义 `cooldown`、`level identity`、`cumulative_loss_cap`、`level_invalidation` |
| News/Macro 事件窗口 | 表已存在未接入；独立立项 |

---

## 8. 下一代策略总体架构

**核心原则：不新建第四套策略栈。** 把已有 research 流水线升级为唯一主干。

```
Binance Testnet (执行真源，不动)
        ↑
┌───────────────────────────────────────────────────────────────┐
│  既有 V2 Execution（PC-01..PC-07 保护，完全不改）              │
│  intent → submit → fill → project → protection → reduce-only  │
└───────────────────────────────────────────────────────────────┘
        ↑ ExecutionIntent
┌───────────────────────────────────────────────────────────────┐
│  既有 Risk Layer（本阶段不调数值）                             │
│  evaluate_entry: kill switch / recon / external position /    │
│  cooldown / drift / expiry                                    │
└───────────────────────────────────────────────────────────────┘
        ↑ 单一候选
┌───────────────────────────────────────────────────────────────┐
│  AI Setup Reviewer（SHADOW，只记录不改单）                     │
└───────────────────────────────────────────────────────────────┘
        ↑
┌───────────────────────────────────────────────────────────────┐
│  CandidateSelectorV2（已存在）方向冲突→拒绝                    │
└───────────────────────────────────────────────────────────────┘
        ↑
┌──────────────┬──────────────────┬─────────────────────────────┐
│ TREND        │ RANGE            │ TRANSITION / FAILURE        │
│ trend_       │ range_sweep_     │ failed_breakout_reversal_v1 │
│ pullback_v2  │ reversion_v1     │ (+ key_level_reversal_v1 待 │
│              │                  │  5m 数据回补后评估)          │
└──────────────┴──────────────────┴─────────────────────────────┘
        ↑ regime 分流
┌───────────────────────────────────────────────────────────────┐
│  RegimeScorerV2（权重待实验确定；BBW/squeeze 并入）             │
└───────────────────────────────────────────────────────────────┘
        ↑
┌───────────────────────────────────────────────────────────────┐
│  MarketContext（point-in-time，已正确）                        │
│  + 新增 market_structure_state（R-4）                         │
│  4h: macro bias │ 1h: regime │ 15m: setup │ 5m: trigger(阻塞) │
└───────────────────────────────────────────────────────────────┘
```

### 8.1 统一结构契约（R-4 目标，研究设计）

```
market_structure_state:
  state: TREND_UP | TREND_DOWN | RANGE | TRANSITION
  last_swing_high / last_swing_low: Decimal
  higher_high_count / higher_low_count / lower_high_count / lower_low_count: int
  bars_since_swing_high / bars_since_swing_low: int
  structure_break: bool          # 是否刚破前一个 swing
  structure_reclaim: bool        # 破后收回
  pivot_window: int              # 显式记录，避免三套定义
  confirmation_lag_bars: int     # 必须显式：pivot 需要右侧 N 根确认
```

> **防泄漏要求**：`confirmation_lag_bars` 必须显式暴露。当前 `dow_trend._collect_pivots` 的循环范围是 `range(pivot_window, len(frame) - pivot_window)`，最新可确认 pivot 至少滞后 `pivot_window` 根。任何使用 swing 的候选**必须**只用已确认 pivot，否则构成 look-ahead。

---

## 9. Trend Pullback 下一版本详细设计（研究设计，非实施）

**基线**：`trend_pullback_v2`（已存在，`RESEARCH_ONLY`）。这是**推荐的第一优先级晋级候选**。

理由：全套基础设施已就位（proposal → replay → walk-forward → adaptive exit）；只依赖 15m/1h/4h（都有 ~12.7 个月数据）；已包含结构止损、缩量确认、cost-adjusted R:R。

### 9.1 时间框架职责

| 周期 | 职责 | 当前实现 | 目标 |
|---|---|---|---|
| 4h | macro bias | `regime.evidence["direction_4h"]` 仅作 0.75 软折扣 | 显式 `macro_bias` + EMA 排列 |
| 1h | regime / setup 许可 | 混在 `RegimeScorerV2` 加权里 | 独立 `regime_state` |
| 15m | setup（回踩 + 缩量 + 确认） | 已实现 | 保留，加 retracement/structure |
| 5m | timing | ❌ **数据缺失** | 阻塞，见第 15 节 |

> **不要求所有周期同时输出 LONG**，而是各自承担不同职责。这是 GAT-01 / COMMON-01 的正确落地方式。

### 9.2 LONG 侧候选逻辑（Short 对称）

```
前置（已实现，保留）:
  15m 无 gap、非 stale、last_closed_at <= decision_time
  ATR14 > 0
  bars >= ema_slow_period + 2

结构与趋势:
  regime.trend_up >= minimum_trend_score (当前 0.60)
  fast_ema(20) > slow_ema(50)
  [新增] macro_bias_4h 不为 OPPOSE
  [新增] market_structure_state in {TREND_UP, TRANSITION}
  [新增] structure_preserved: 回踩未破 last_swing_low

回踩质量:
  pullback.low <= fast_ema  且  pullback.close >= slow_ema   (已实现)
  [新增] retracement_ratio = pullback_depth / impulse_length   (FIB-01/02 feature)
  [新增] impulse_length_atr = impulse_distance / ATR14
  pullback.volume < mean_volume_10                            (已实现, DOW-03)
  [新增] pullback_volume_ratio 连续值（不只是布尔）

确认（已实现，保留）:
  confirmation.high > pullback.high
  confirmation.close > pullback.high
  confirmation.close - fast_ema <= ATR14 * maximum_entry_distance_atr

[新增] 动量确认 (MACD-02/03):
  macd_histogram_slope > 0        # 柱体扩张
  cross_age <= N bars             # 交叉不过期（当前 legacy 回溯 6 根是反例）

成本闸门（已实现，保留）:
  cost_adjusted_rr = (|tp2 - entry| - cost) / (risk + cost) > 0
```

### 9.3 无效条件（invalidation）

- `stop = pullback.low - ATR14 * stop_atr_buffer(0.25)` — 结构锚定（COMMON-05 / TL-04），**保留**。
- `extreme_price = pullback.low` — 结构失效参考。
- 候选过期：`expires_at = signal_time + 15m * expiry_bars(2)`。
- [新增] `structure_invalidated`：回踩深度超过 impulse 起点（WAVE-03 第二浪不破起点的**可量化残留**，不依赖浪号）。

### 9.4 目标（当前实现，作为 Exit 实验基线）

`tp1 = 1.0R (35%)` / `tp2 = 1.8R (40%)` / `tp3 = 2.5R (25%)`。

---

## 10. Range Sweep 设计

**基线**：`range_sweep_reversion_v1`（已存在，Donchian-24 扫破后收回）。

**核心约束**：**震荡环境不得复用 trend-following 逻辑**，反之亦然。

| 要素 | 当前 | 目标 |
|---|---|---|
| 区间边界 | Donchian-24 | + `touch_count`、`boundary_stability` |
| 压缩识别 | 无 | BBW percentile + `regime.compression`（BB-02） |
| 扫单确认 | 收回区间内 + 次根确认 | + `penetration/ATR`、`wick/ATR` |
| 成交量 | 无 | `sweep_volume_ratio`、OBV 背离（BB-03，RESEARCH_ONLY） |
| 准入 | `regime.trend_*` | 必须 `regime.range` 主导才允许 |

**明确禁止**：TREND 环境下因"触及 Bollinger 上轨"直接做空（BB-01）。当前 `trend_momentum_v2_enriched` 把 `bollinger` 当趋势入场信号，正是这个错误的实例。

---

## 11. Failed Breakout / Key Level Reversal 设计

### 11.1 结论：先独立，不合并

`failed_breakout_reversal_v1`（已存在，15m Donchian-24）与 Gatsby 的 `key_level_reversal_v1`（4h 关键位 + 5m 触发）**语义相近但不等价**：

| 维度 | failed_breakout_reversal_v1 | key_level_reversal_v1 (Gatsby) |
|---|---|---|
| 关键位来源 | 15m 滚动 Donchian-24 | **4h** swing / 多次触碰 S/R |
| 触发周期 | 15m 次根确认 | **5m** 裸 K 反转 |
| 位级别 | 自动滚动，无强度概念 | 有 `touch_count` / `age` / `strength` |
| 放弃条件 | 无 | 5m 大 K 彻底贯穿（GAT-07） |

**决策**：**保持独立**。理由：若合并，日后无法判断收益来自 15m 滚动边界还是 4h 关键位——这正是 Prompt 第 35 节要求避免的归因污染。

### 11.2 Gatsby 关键位量化定义（研究设计）

```
key_level (4h):
  level_price: Decimal
  level_type: SWING_HIGH | SWING_LOW | BREAKOUT_RETEST | SR_FLIP
  touch_count: int                    # >= 2 才算有效（TL-01 "价格已三次遵循该线"）
  age_bars: int
  distance_atr: Decimal               # 当前价到位的 ATR 归一距离
  level_strength: float               # 由 touch_count / age / 反应幅度合成

reversal_trigger (5m):  ← 5m 数据缺失，当前无法实现
  trigger_type: ENGULFING | PIN_BAR | REJECTION_WICK | FAILED_PENETRATION | RECLAIM
  body_atr = |close-open| / ATR
  wick_ratio = wick / body
  close_location = (close-low)/(high-low)
  penetration_atr = |extreme - level_price| / ATR
  micro_structure_break: bool

valid_breakout（放弃开关，GAT-07）:
  body_atr >= θ1  AND  penetration_atr >= θ2  AND  close_beyond_level
  AND volume_ratio >= θ3  AND follow_through
  → 阈值 θ 必须来自历史分布分位数，禁止拍脑袋
```

### 11.3 retry 的强制边界（GAT-06）

**默认 `max_retries = 0`。** 必须先独立证明：

| 组 | 需独立满足 |
|---|---|
| attempt 1 | 正 expectancy（基线） |
| attempt 2 | 独立正 expectancy，且 `cumulative_loss` 未超上限 |
| attempt 3 | 同上 |

必须同时定义：`cooldown_bars`、`same_level_identity`（如何判定"还是同一个位"）、`cumulative_loss_cap`、`level_invalidation`（GAT-07 触发即永久放弃该位）。

> **风险提示（已向操作员标注）**：当前 `risk_per_trade=0.05`。若允许同一关键位 3 次尝试且全部止损，单一位点可累积 **10-15% 账户回撤**。若 attempt-2/3 无独立正 expectancy，**必须删除 retry**，不得以"视频这样讲"为理由保留。

---

## 12. AI Setup Reviewer 设计

### 12.1 AI 当前真实权限（实测）

```
AI currently sees:      symbol, price, candidate side, confidence,
                        stop_distance, take_profit_distance   ← 仅 6 项
                        (_build_trade_prompt, ai_review_service.py:133-143)
AI currently decides:   什么都不决定
AI currently can veto:  ❌ 不能。advisory_veto 属性存在，但
                        EntryRuntimeContext.ai_advisory_veto 从未被赋值
                        (cycle_service.py:1528-1543 未传该字段)
AI can alter risk:      ❌ 不能
AI failure behavior:    fail-open（provider 失败继续确定性执行）✅ 正确
AI latency budget:      v2_ai_review_budget_seconds，默认 1.5s
                        超时返回 ai_review_budget_exceeded
实测调用质量:            passed 383 / failed 395 / provider_unavailable 1091
落库字段:                无 bias / confidence / risk_flags   ← 归因阻塞
```

### 12.2 目标 input schema（研究设计）

**前提：确定性策略必须先产生 Candidate。没有 Candidate，AI 永远不能创造交易。**

```json
{
  "symbol": "BTC/USDT",
  "decision_time": "ISO8601",
  "macro_4h":   {"bias": "...", "structure_state": "...", "ema_alignment": "...", "regime": {...}},
  "regime_1h":  {"trend_score": 0.0, "range_score": 0.0, "expansion": 0.0, "compression": 0.0},
  "setup_15m":  {"setup_type": "trend_pullback_reclaim", "impulse_length_atr": 0.0,
                 "retracement_ratio": 0.0, "pullback_volume_ratio": 0.0,
                 "ema_relation": "...", "structure_state": "...",
                 "key_level": {"type": "...", "distance_atr": 0.0, "touch_count": 0}},
  "trigger_5m": {"available": false, "reason": "5m_data_missing"},
  "momentum":   {"macd_histogram": 0.0, "histogram_slope": 0.0, "cross_age_bars": 0},
  "volatility": {"atr14": 0.0, "bbw": 0.0, "bbw_percentile": 0.0},
  "derivatives":{"available": false, "reason": "market_extras_symbol_mismatch"},
  "execution":  {"expected_drift_bps": 0.0, "estimated_fee_bps": 0.0, "cost_adjusted_rr": 0.0},
  "portfolio":  {"current_exposure": 0.0, "same_symbol_position": "...",
                 "unmanaged_external_position": true}
}
```

> `available: false` 字段**必须显式保留**，不得静默省略——否则 AI 会把"没数据"当成"数据正常"。

### 12.3 目标 output schema

```json
{"setup_quality": 0.0, "bias": "SUPPORT|NEUTRAL|OPPOSE", "confidence": 0.0,
 "context_flags": [], "risk_flags": [], "reason_codes": [], "summary": ""}
```

### 12.4 第一阶段权限（不变）

只记录。不改订单、不改方向、不改仓位、不改 SL、不改 TP。

### 12.5 失败模式

| 模式 | 行为 |
|---|---|
| provider 不可用 | fail-open，记录 `provider_unavailable`（现状正确，保留） |
| 超预算 | fail-open，记录 `ai_review_budget_exceeded` |
| JSON 解析失败 | 降级 NEUTRAL（`_parse_trade_response:155`，现状正确） |
| 强制退出进行中 | SKIPPED `FORCED_EXIT_IN_PROGRESS`（**AI 绝不阻塞 hard exit**，保留） |

### 12.6 Shadow 评估与晋级条件

对 closed trades 分组比较 net expectancy / avg R / PF / win rate / MaxDD / MAE / MFE：

需长期出现 `SUPPORT > NEUTRAL > OPPOSE` 且 OOS 稳定，才可研究 veto 权限。即使开放：**AI 只能减少风险或拒绝交易，不得创造交易、不得提高杠杆、不得突破确定性风险上限。**

**前置阻塞**：需先给 `llm_invocations` 增加 bias/confidence/risk_flags 列（第 20 节 R-03）。**当前样本 1 笔已平仓交易，即使 schema 修好也远不足以评估。**

### 12.7 Feature-only vs Feature+Bars（A/B）

| 方案 | 内容 | 优点 | 风险 |
|---|---|---|---|
| A | 仅结构化 feature | 稳定、低 token、易 replay | 可能丢失形态信息 |
| B | feature + 最近 N 根 OHLCV | 保留形态 | token/latency 上升；**必须固定 bar 数、固定序列格式、严禁未来 K** |

若 B 未提高 OOS 预测价值，**不采用**（不浪费 token 和 latency）。

---

## 13. Entry 实验矩阵（消融）

**Control（Experiment 0）**：冻结当前真实 Active = `testnet_sampling_v2`（15m EMA50 + MACD柱 + RSI 区间，SL `max(1.2ATR, 0.35%)`，TP `1.5R`）。

> **注意**：Prompt 原文假设 Control 是 `trend_momentum_v2_enriched`。经代码核实，**真实在交易所下单的是 `testnet_sampling_v2`**。为可归因，建议同时冻结两个 Control：
> - **C0-A** = `testnet_sampling_v2`（真实 Active，唯一有真实成交的）
> - **C0-B** = `trend_momentum_v2_enriched`（manifest 声称的，legacy replay）

| 实验 | 增量 | 验证的视频思想 | 依赖 |
|---|---|---|---|
| E0 | Control 冻结 | — | 无 |
| E1 | `trend_pullback_v2` baseline | EMA-02 / TL-03 / COMMON-04 | 无 |
| E2 | + `market_structure_state` 精修 | DOW-02 / TL-03 | R-4 |
| E3 | + `retracement_ratio` 分档 | FIB-01 / FIB-02 / WAVE-04 | E2 |
| E4 | + MACD 动量恢复（`hist_slope`、`cross_age`） | MACD-02 / MACD-03 | E3 |
| E5 | + 成交量确认（`pullback_volume_ratio`；可选 OBV） | DOW-03 / DOW-05 / BB-03 | E4 |
| E6 | + 5m 入场时机 | GAT-01 / GAT-03 | **阻塞：5m 数据** |
| E7 | + AI Shadow score | 第 12 节 | R-03 schema |
| E8 | Regime 权重反转（4h 主导） | COMMON-01 / DOW-01 | 独立可跑 |
| E9 | 指标去重（bollinger/rsi 移出方向投票） | BB-01 / COMMON-02 | 独立可跑 |

**规则**：只有前一个实验证明增量价值才进入下一个。禁止一次加入 7 个条件后宣布全部有效。无法证明边际贡献的 feature 一律删除。

### 13.1 相关性前置检查（E9 的必要前提）

先计算 `ema_trend` / `dow_trend` / `mtf_ma` / `macd` 方向序列的两两相关。若 |ρ| > 0.7，**不得当作独立证据计票**。当前 `weighted.py:103` 的加权平均没有任何去相关处理。

---

## 14. Exit 实验矩阵（独立于 Entry）

**必须在 Entry 稳定后再做，不得同时进行。**

**Control**：Active 现状 = 固定 SL（ATR/固定取大）+ `1.5R` TP，无 partial、无 trailing、无保本。

| 组 | 规则 | 来源 |
|---|---|---|
| X0 | Control（fixed 1.5R） | 现状 |
| X1 | fixed 2.0R | registry 声称值 |
| X2 | partial TP 0.35@1R / 0.40@1.8R / 0.25@2.5R | `AdaptiveExitPlan` 现有 |
| X3 | **+3R 移动止损保本** | **GAT-05（视频规则，仅作实验组）** |
| X4 | ATR trailing（`trailing_activation_r=1.8`, `atr_multiple=2`） | 现有 research |
| X5 | 结构 trailing（跟随 swing） | COMMON-05 / TL-04 |
| X6 | 时间退出（`time_exit_bars=8`, `min_r=0.5`） | 现有 research |

> **明确不假定 3R 保本最好。** 视频这样讲，但必须由 BTC/ETH 自己的数据决定。历史上 `ExitLadder` 就是被自己的 replay 证明严格劣于 fixed-2R 后回退的（`docs/audits/2026-07-12-exitladder-replay-comparison.md`）。

---

## 15. Backtest / Walk-forward 设计（基于真实数据现状）

### 15.1 实测数据覆盖（`.local_paper_console.db`，2026-08-08 查询）

| symbol | tf | bars | first_open | last_open | 可用? |
|---|---|---|---|---|---|
| BTC/USDT | 15m | 37,134 | 2025-07-17 20:00 | 2026-08-08 15:15 | ✅ ~12.7 月 |
| BTC/USDT | 1h | 9,284 | 2025-07-17 20:00 | 2026-08-08 15:00 | ✅ |
| BTC/USDT | 4h | 2,321 | 2025-07-17 20:00 | 2026-08-08 12:00 | ✅ |
| BTC/USDT | 1m | 24,277 | **2026-07-15 22:42** | 2026-08-08 15:15 | ⚠️ 仅 24 天 |
| BTC/USDT | **5m** | **0** | — | — | ❌ **完全缺失** |
| ETH/USDT | 15m/1h/4h | 同 BTC | 2025-07-17 | 2026-08-08 | ✅ |
| ETH/USDT | **5m** | **0** | — | — | ❌ **完全缺失** |
| SOL/USDT | 15m/1h/4h | 同 BTC | 2025-07-17 | 2026-08-08 | ✅（非执行范围） |
| market_extras | — | ~10.6k/符号 | 2026-07-26 05:12 | 2026-07-26 08:32 | ❌ 仅 3.3 小时 + 符号不匹配 |

`baseline/data_manifest.json` 亦记录 `status: DATA_COVERAGE_INSUFFICIENT`、`required_total_months: 42`、5m `present: false`。

### 15.2 三个硬阻塞

| ID | 阻塞 | 影响 | 处理 |
|---|---|---|---|
| **B-01** | 5m bars = 0 | Gatsby 5m 触发（E6、`key_level_reversal_v1`）**无法研究** | 需回补 5m 历史（数据缺口，非策略问题） |
| **B-02** | 仅 ~12.7 月 < 42 月 | `build_proposal_walk_forward_windows` 硬校验 `window_count==8 and train_months==12 and oos_months==3`（`proposal_walk_forward.py:54-55`），当前数据**会直接抛异常** | 二选一：回补历史至 42 月，**或**由操作员批准缩减窗口方案（需改硬校验 → 需批准） |
| **B-03** | 1 笔已平仓交易 | 任何实盘统计无意义 | 全部结论走 replay；Testnet 只证明链路 |

### 15.3 可行的 walk-forward 方案（待操作员选择）

**方案 A（推荐，无需改代码，需回补数据）**：回补 BTC/ETH 15m/1h/4h 至 2023-01 起（约 42 月），沿用现有 8×3 月 OOS + 12 月训练 + 6 月 holdout。

**方案 B（用现有数据，需改硬校验 → 需批准）**：在 ~12.7 月内做 4×1 月 OOS + 6 月训练。样本量将非常小，**必须在报告中标注置信区间是否跨 0**，且不足以支撑晋级。

> 未经操作员批准，**不得擅自修改 `proposal_walk_forward.py` 的窗口硬校验**——那等于放宽验证门槛。

### 15.4 防泄漏检查清单

| 风险 | 当前状态 |
|---|---|
| 未收盘 bar 进入决策 | ✅ 已防（`_closed_window`: `bar.timestamp + delta <= decision_time`） |
| next-bar-open 成交 | ✅ 已实现（Phase 1 parity：closed-bar signal → next-bar-open fill） |
| 末根无后续 bar 造假成交 | ✅ 已防 |
| **pivot / swing 确认滞后** | ⚠️ **需显式检查**：`_collect_pivots` 需右侧 `pivot_window` 根确认 |
| **trendline 锚点** | ⚠️ 若实现 TL-01，必须只用已确认 pivot |
| **Fibonacci 锚点** | ⚠️ impulse swing 必须已确认，不得用未来极值 |
| **divergence** | ⚠️ 背离需两个已确认极值点 |
| repainting 指标 | ⚠️ 任何"随新 bar 改变历史标签"的指标必须拒绝（Elliott Wave 即因此 REJECT） |
| survivorship | ✅ BTC/ETH 固定范围，无选样偏差 |

### 15.5 成本模型

必须纳入：maker/taker fee、spread、slippage、funding、min quantity、quantity precision。

现状：
- `entry_rules`: `core_fee_bps=5.0` / `core_slippage_bps=1.0` / `standard_slippage_bps=3.0`（已对齐币安 USDM 常规费率 maker 2bps / taker 5bps）。
- `trend_pullback_v2`: `expected_round_trip_cost_bps=12`。
- `baseline/cost_model.json` 明确标注 legacy replay **未建模** funding / latency / partial_fill / spread。
- `proposal_replay.py` 已按持仓期真实结算事件计 funding。

**禁止只比较毛利润。优先比较 cost-adjusted expectancy。**

> 记录一条历史教训：此前 10-18bps/边的假设是真实费率的 2-4 倍，导致 `net_edge_after_cost_negative` 误杀大量本应通过的信号。调整费率假设是为了让门槛**准确**，不是为了放宽门槛。

---

## 16. Evaluation Metrics

**主要优化目标：Net Expectancy After Costs。** 禁止以"胜率最高"或"总收益最高"为唯一目标。

必须统计：Trade Count、Win Rate、Average R、Median R、Average Win R、Average Loss R、Profit Factor、Net PnL、Net Expectancy、Max Drawdown、Worst Losing Streak、Tail Loss、MFE、MAE、Holding Time、Turnover、Fees、Slippage、Fees/Gross PnL。

必须分层报告：BTC / ETH · Long / Short · Trend / Range / Transition · Setup Family · Volatility Regime。

**样本量纪律**：必须显式写出样本量。35-65 笔 OOS 不足以支撑"策略有效"，报告置信区间时必须说明是否跨 0。

---

## 17. Promotion Gates

### Research → Shadow

| 条件 | 说明 |
|---|---|
| 样本量达到最低要求 | 具体数值由 walk-forward 方案确定后写入；**不得在没有数据前拍脑袋写"PF > 2 / 胜率 > 70%"** |
| cost-adjusted expectancy > Control | 对 C0-A 和 C0-B 都要报 |
| PF 不恶化 | — |
| MaxDD 可接受 | 对齐既有门槛 `MaxDD < 25%` |
| OOS 为正 | — |
| 多个 walk-forward window 稳定 | 不能只靠 1 个窗口 |
| 非单一币种贡献全部收益 | BTC/ETH 分开报 |
| 无参数敏感悬崖 | 邻域参数扫描 |

> **严禁为了让候选通过而调整门槛数值本身**（`Sharpe > 1.0` / `PF > 1.3` / `MaxDD < 25%` / `Expectancy > 0`）。认为门槛不合理必须先征得操作员同意。

### Shadow Gate

产生 candidate、记录本应交易结果、**不下真实订单**。必须记录：candidate inputs、feature snapshot、AI snapshot、expected entry/stop/target、后续 MFE、MAE、hypothetical R。

**前置**：需要 R-1（ACTIVE 下也产出 shadow）。

### Testnet Gate

Shadow 胜出后才可 Testnet，且**仍使用当前 V2 execution**。策略晋级期间**禁止同时**修改 execution / risk engine / order semantics——否则无法归因。

> **现实约束**：手动基线（`BTC short 0.5302` / `ETH short 6.814`）在位期间，`UNMANAGED_EXTERNAL_POSITION` 已拦掉 110 次候选。Testnet 阶段的成交密度受此限制，**这是预期行为**（用户明确要求手动仓位不平），不是 bug，也不应通过放宽隔离来"解决"。

### Active Gate

需操作员显式批准 + 更新 active manifest（含 `report_path` 指向真实 OOS 报告，不得再为 null）。

---

## 18. 文件级修改地图

### MUST_NOT_CHANGE（保护契约）

| 文件 | 当前职责 | 为何不能改 |
|---|---|---|
| `services/automated_trading/application/cycle_service.py` 的 submit/fill/project/protection 段 | exchange-first 执行 | PC-01 / PC-02 / PC-03 |
| `services/automated_trading/application/exit_service.py` | reduce-only 退出 | PC-03；**任何检查都不得阻塞 hard exit** |
| `services/automated_trading/infrastructure/runtime_lock.py` | 单写入者 + mainnet 拒绝 | PC-06 |
| `services/automated_trading/infrastructure/binance_adapter.py` | 交易所回执 | PC-02 |
| `services/automated_trading/application/reconciliation_service.py` | 对账 | Gate 16/17 冻结 |
| `services/automated_trading/application/protection_service.py` | 真实 SL/TP | PC-03 |
| `services/execution/paper_*.py`（4 个） | legacy 冻结链 | AGENTS.md 明确冻结 |
| `entry_service.py` 的 drift / kill switch / external position 逻辑 | 风控闸门 | PC-05；`UNMANAGED_EXTERNAL_POSITION` 保护手动仓位 |
| `risk_per_trade` / `max_leverage` / `max_position_fraction` / 止损止盈数值 | 风险参数 | PC-04，本阶段不动 |

### MUST_CHANGE（实施阶段，需按任务批准）

| 文件 | 当前职责 | 目标职责 | 具体函数 | 为什么 | 影响 | 不能改变什么 |
|---|---|---|---|---|---|---|
| `services/execution/v2_scheduler_entry.py` | ACTIVE 下不算 shadow | ACTIVE 下也算 shadow（只记录） | L361 `if config.v2_activation is EngineActivation.SHADOW` | R-1：拿对照数据 | 增加 cycle 延迟 | 绝不让 shadow 结果参与下单；不得超 `pretrade_max_decision_age_seconds=75` |
| `services/strategy_library/regime/scorer_v2.py` | 15m 主导权重 | 权重可配置 + 实验 | `DIRECTION_WEIGHTS`、`_direction()` | R-2：COMMON-01 | 影响三候选准入 | 不得在无 A/B 证据下直接改默认值 |

### MAY_CHANGE（研究期，`RESEARCH_ONLY` 范围内）

| 文件 | 目标 | 备注 |
|---|---|---|
| `services/strategy_library/candidates/trend_pullback_v2.py` | E2-E5 增量 feature | 已是 `RESEARCH_ONLY` / `execution_eligible=False` |
| `services/strategy_library/candidates/range_sweep_reversion_v1.py` | 第 10 节增量 | 同上 |
| `services/strategy_library/candidates/failed_breakout_reversal_v1.py` | 第 11 节增量 | 同上 |
| `services/strategy_library/context.py` | 新增 `market_structure_state` | **只新增字段，不改 `_closed_window` 的 point-in-time 语义** |
| `services/validation/proposal_replay.py` | 消融/分层指标 | 不改 next-bar-open 语义 |
| `services/strategy_library/exit/adaptive_exit.py` | Exit 实验组 | research only |

### NEW_FILE_IF_REQUIRED

| 路径 | 用途 | 条件 |
|---|---|---|
| `services/strategy_library/technical/market_structure.py` | 统一 swing/pivot/结构状态（R-4） | 需显式 `confirmation_lag_bars` |
| `services/strategy_library/technical/key_levels.py` | 4h 关键位（GAT-02） | — |
| `services/strategy_library/candidates/key_level_reversal_v1.py` | Gatsby 反转候选 | **B-01 解除后** |
| `scripts/backfill_5m_history.py` | 回补 5m | B-01 |
| `scripts/ablation_entry_matrix.py` | E1-E9 消融 | — |

### NEEDS_CODE_MAPPING

| 项 | 原因 |
|---|---|
| `llm_invocations` 加 bias 列的 Alembic 迁移 | 需确认迁移链 head，且 DB schema 变更需批准 |
| `market_extras` 符号映射修复点 | 需确认是写入侧（`BTC/USDT:USDT`）还是读取侧（`BTC/USDT`）为准，涉及数据层约定 |
| 5m 数据来源 | 需确认 backfill 走既有 `services/data/binance.py` 还是新脚本 |

---

## 19. 实施任务拆分

每个任务只承担一个清晰目的。**T0 是所有其他任务的前置。**

| ID | 任务 | 目的 | 前置 | 阻塞状态 |
|---|---|---|---|---|
| **T0** | 数据缺口修复：回补 5m；修 `market_extras` 符号映射；确定 walk-forward 方案 A/B | 解除 B-01/B-02/R-7 | — | **需操作员决策** |
| T1 | Market Structure Features：统一 pivot/swing/结构状态 | R-4；DOW-02、FIB-03 | T0（可部分并行） | — |
| T2 | Trend Pullback Research Upgrade（E2） | 第 9 节 | T1 | — |
| T3 | Key Level + 真假突破动能特征（`body/ATR` 等，TL-02/GAT-02/GAT-07） | 第 11 节 | T1 | 5m 部分被 B-01 阻塞 |
| T4 | Fibonacci Retracement Feature（E3） | FIB-01/02 | T2 | — |
| T5 | MACD Feature Refactor（`hist_slope`、`cross_age`，E4） | MACD-02/03 | T2 | — |
| T6 | Volume Feature（含可选 OBV，E5） | DOW-03、BB-03 | T2 | — |
| T7 | AI Shadow Reviewer（input/output schema + 落库 bias） | 第 12 节 | R-03 批准 | schema 变更待批 |
| T8 | Replay / Ablation 框架（E1-E9 + Exit X0-X6） | 第 13/14 节 | T0 定方案 | — |
| T9 | Shadow 并行产出（R-1） | 拿对照数据 | — | 需延迟预算实测 |
| T10 | Regime 权重实验（E8） | R-2、COMMON-01 | T8 | — |
| T11 | 指标去重 + 相关性分析（E9） | R-3、BB-01、COMMON-02 | T8 | — |

**建议起步顺序**：T0 → (T9 ∥ T1) → T2 → T8 → T4/T5/T6 逐个消融 → T10/T11 → T3 → T7。

---

## 20. 风险与停止条件

### 20.1 需操作员决策的项（本阶段不自行推进）

| ID | 项 | 为什么需要批准 |
|---|---|---|
| **R-01** | V2 Active 中 `net_edge_after_cost_bps` / `manifest_eligible` / `ai_advisory_veto` 从未赋值 ⇒ 三个闸门结构性失效 | 接线会**新增拦截**，改变现有下单行为。属"修改 net_edge/gatekeeper 准入"范畴 → 高风险，需明确同意 |
| **R-02** | walk-forward 方案 A（回补 42 月数据）vs B（缩减窗口，需改硬校验） | B 等于放宽验证门槛 |
| **R-03** | `llm_invocations` 增加 bias/confidence/risk_flags 列 | 数据库 schema 变更 + Alembic 迁移 |
| **R-04** | `market_extras` 符号映射以写入侧还是读取侧为准 | 影响数据层约定 |
| **R-05** | active manifest 指向 `trend_momentum_v2_enriched` 但 V2 Active 实际执行 `testnet_sampling_v2`，且 `report_path: null` | manifest 语义与现实不符，需确认是修 manifest 还是修 V2 接线 |

### 20.2 STRATEGY_SCOPE_CONFLICT

以下情形一旦出现，立即停止并上报，不得继续实施：

| 触发 | 说明 |
|---|---|
| 需修改 Gate17 execution 才能做策略实验 | 已冻结 |
| 需改数据库核心状态机 | — |
| 需改 single writer | PC-06 |
| 需改真实 fill semantics | PC-02 |
| 需改 risk sizing | PC-04 |

**本次已识别的 scope 冲突**：

- **SC-01**：真实成交密度受手动基线限制（110 次 `UNMANAGED_EXTERNAL_POSITION`）。若有人提出"为提高样本量而放宽外部仓位隔离"，**这是 scope 冲突**，必须拒绝——用户已明确要求手动仓位保留不平。
- **SC-02**：R-01 的三个失效闸门，修复涉及风控准入语义 ⇒ 归入 R-01 等待批准，不在策略研究任务内顺手改。

### 20.3 NON_PROGRAMMABLE_OR_UNVERIFIABLE

| 项 | 原因 |
|---|---|
| Elliott Wave 浪号自动标注 | 标签不稳定、可重画、回测不可复现 |
| 江恩角度线 / 九方图 / 轮中轮 | 锚点需人工挑选，不可复现 |
| 江恩时间周期（45/90 天、周年、节气） | 无法在 12.7 月数据上做有效 OOS |
| Gatsby "这根反转 K 之后价格再也没回来过" | **定义上使用了未来信息**（要知道"之后没回来"必须看未来 bar）。只能改写为"入场后 N 根内 MAE = 0"的**事后统计指标**，不能作为入场条件 |
| "看起来很强" / "疲弱小 K" 的主观判断 | 必须先转成 `body/ATR` 等可量化特征，阈值来自历史分布 |

### 20.4 停止条件

- 同一失败重复 2 次无进展 → 停止，上报证据。
- 每个失败检查最多 3 次自动修复。
- 任何实验若需放宽既有门槛数值才能"通过" → 停止，上报。

---

## 21. 最终推荐

**只推荐一个优先晋级 Setup：`trend_pullback_v2`（趋势回踩）。**

理由：

1. **基础设施已完备**：proposal → replay（next-bar-open parity）→ walk-forward（purge+embargo）→ adaptive exit 全链路已存在且有测试。
2. **数据可用**：只依赖 15m/1h/4h，都有 ~12.7 个月连续数据；不受 5m 缺失（B-01）阻塞。
3. **已正确实现视频最核心的四条**：EMA 回踩定位（EMA-02）、次根确认不抢先（COMMON-04）、结构止损（COMMON-05/TL-04）、回调缩量（DOW-03）。
4. **增量路径清晰**：E2→E3→E4→E5 逐个消融，每步对应一条明确的视频假设。
5. **成本闸门已内置**：`cost_adjusted_rr <= 0` 直接返回 None，符合"优先 cost-adjusted expectancy"。

**明确不同时推荐**：`range_sweep_reversion_v1` 和 `failed_breakout_reversal_v1` 保持 `RESEARCH_ONLY` 待第二批；`key_level_reversal_v1`（Gatsby）在 5m 数据回补前**不立项**。

### 但在任何策略实验开始前，必须先完成 T0

当前状态下直接跑实验会得到无效结论：

| 若跳过 | 会得到的假结论 |
|---|---|
| 不修 `market_extras` 映射 | "funding / OI / long-short 对策略无用" |
| 不解决 walk-forward 窗口 | 代码直接抛异常，或用远不足的样本得出"有效" |
| 不回补 5m | "Gatsby 策略无法实现"（实际是数据缺口） |
| 不打通 ACTIVE 下 shadow | 永远没有与真实执行同期的对照组 |

---

## 附录 A：本次研究的证据边界

**已实际执行的验证**：

```
[验证] Read 代码       -> decision_service.py / cycle_service.py / entry_service.py /
                          v2_scheduler_entry.py / scheduler.py / runtime_lock.py /
                          proposal_pipeline.py / scorer_v2.py / selector_v2.py /
                          weighted.py / family/selector.py / context.py /
                          trend_pullback_v2.py / adaptive_exit.py / ai_review_service.py /
                          indicators.py / macd.py / dow_trend.py / price_action.py /
                          costs.py / proposal_walk_forward.py / registry.py
[验证] SQL 查询        -> .local_paper_console.db：ohlcv 覆盖、market_extras 符号、
                          v2_execution_cycles/decisions 漏斗、v2_managed_positions、
                          v2_exchange_fills、llm_invocations 分布、position_records
[验证] grep 交叉确认   -> net_edge_after_cost_bps= 全仓无赋值点；
                          ai_advisory_veto 仅 3 处（定义/判断/测试）；
                          active-manifests 目录仅 1 个文件
[验证] 文件读取        -> docs/evidence/active-manifests/auto_paper_mature_templates.json
                          artifacts/strategy_refactor/baseline/{data,cost,metrics}_*.json
                          D:\douyin_research\整合稿_技术分析体系.md（全文 574 行）
```

**未执行 / 未验证**：

- 未运行 ruff / mypy / pytest（本次为只读研究，未改任何代码，无需回归）。
- 未读取 `.env`（工具权限拒绝，改用 `shared/config.py:52` 默认值 `legacy` + `runtime_lock.py` 映射逻辑推断激活语义）。**因此"当前实际运行在 v2_active"这一点未由 `.env` 直接证实**，是由 `当前状态.md` 记录的 "V2 `ACTIVE`" + DB 中 13,379 条 `v2_execution_cycles` 且 `execution_mode=BINANCE_TESTNET` 的真实成交推断。
- 未读取 9 条视频的原始 transcript（整合稿对各条规则表达已足够清晰，按 Prompt 第三节要求未做无边界重读）。
- 未访问 Final Holdout，未运行任何回测 / DSR / PBO / bootstrap。
- 未修改任何生产代码、未 promote、未启动新 Testnet 策略、未改仓位/杠杆/风控数值、未创建分支或 worktree。

**本文件是研究设计，不是实施授权。** 每个任务实施前需单独 Prompt 并获操作员批准。
