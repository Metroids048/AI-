# Agent 执行 Prompt：加密货币策略收口重构

你正在仓库：

```text
https://github.com/Metroids048/AI-
```

目标分支：

```text
fix/v2-production-closure
```

你的任务不是继续做全面分析，也不是重构整个项目，而是在现有生产闭环修复基础上，对**量化策略内核进行一次收口式重构**。

---

## 一、唯一目标

建立一套 BTCUSDT、ETHUSDT Binance Futures Testnet 策略系统。

策略只有满足以下最终门槛才能进入 `ACTIVE_TESTNET`：

```text
胜率 >= 50%
平均盈利 / 平均亏损 >= 1.20
Profit Factor >= 1.50
手续费、滑点、资金费率后的单笔净期望 > 0
组合最大回撤 <= 15%
95% 单侧净期望置信区间下界 > 0
```

注意这些门槛是联合约束。不得分别擦线后宣称通过。

无法找到合格策略时，必须输出：

```text
NO_ACTIVE_STRATEGY
```

这不是失败。强行放行不合格策略才是失败。

---

## 二、收口模式

严格遵守：

1. 禁止全面架构重构；
2. 禁止替换现有执行引擎；
3. 禁止迁移到 Freqtrade、Lean、NautilusTrader、vn.py 等完整框架；
4. 禁止修改前端；
5. 禁止修改交易所订单状态机，除非为了适配稳定 StrategyCandidate/TradeIntent 合同且有失败测试；
6. 禁止“顺手优化”无关代码；
7. 禁止继续在旧 4h/1h/15m 硬过滤链上打补丁；
8. 禁止大规模 Hyperopt；
9. 禁止强化学习；
10. 禁止 AI 直接生成自由价格、数量或覆盖硬风控；
11. 禁止以 Testnet Sampling 交易作为盈利证据；
12. 每个任务单独测试、单独提交；
13. 发现新问题先记录，只有阻塞本任务验收时才修复；
14. 不得扩大任务范围。

---

## 三、开始前必须审计真实分支

先执行：

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log -10 --oneline
git diff --stat main...HEAD
git diff --name-only main...HEAD
```

然后完整阅读，不允许只搜索关键词：

```text
AGENTS.md
CURRENT_STATE.md

services/strategy_library/**
services/validation/**
services/agents/**
services/execution/decision_pipeline.py
services/execution/paper_signal.py
services/execution/bootstrap.py
services/execution/exit_ladder.py
services/execution/net_edge.py
services/execution/signal_edge_stats.py

所有对应测试
所有 Active Manifest 和当前策略报告
```

额外确认当前生产闭环任务已修改哪些文件。策略重构不得覆盖它们的未完成修改。

先输出 `Strategy Refactor Audit`：

```text
当前 commit:
当前测试基线:
当前活跃策略:
当前策略入口:
当前 StrategyCandidate/TradeIntent 合同:
当前回测入口:
当前 AI 调用条件:
当前固定 4h/1h/15m Gate:
当前固定退出规则:
与生产闭环任务冲突文件:
计划保留文件:
计划新增文件:
计划窄修改文件:
计划停用策略:
```

在审计完成前，不得改代码。

---

## 四、先保存黄金基线

使用现有代码和当前数据生成不可变基线：

```text
artifacts/strategy_refactor/baseline/
```

必须包含：

- Git SHA；
- 数据范围与哈希；
- 配置哈希；
- 活跃策略；
- 逐笔交易；
- 成本模型；
- 胜率；
- 平均盈亏比；
- PF；
- 成本后净期望；
- 最大回撤；
- 当前 CI；
- 每个 Gate 的通过/拒绝数量；
- BTC、ETH 和组合结果。

不得通过修改旧报告生成器来美化基线。

---

## 五、策略架构

保留现有数据、验证和执行边界，在 `StrategyCandidate/TradeIntent` 之前替换策略内核。

目标流水线：

```text
Point-in-time MarketContext
-> RegimeScore
-> 3 个独立 StrategyProposal
-> CandidateSelector
-> AI Market Committee
-> Deterministic TradePlan
-> 现有 DecisionPipeline / TradeIntent
-> 现有生产闭环执行
```

4h/1h 不再是全局硬 Gate，只是 `RegimeScore` 的上下文输入。

15m 为首发信号周期。

1m/5m 仅用于：

- 最新价格确认；
- 入场触发；
- 决策过期；
- 价格偏移；
- 执行新鲜度。

---

## 六、稳定数据类型

优先扩展现有模型；只有现有职责确实不匹配才新建文件。

实现或适配：

```python
class MarketContext(BaseModel):
    symbol: str
    decision_time: datetime
    bars_1m: BarWindow
    bars_5m: BarWindow
    bars_15m: BarWindow
    bars_1h: BarWindow
    bars_4h: BarWindow
    structure: StructureFeatures
    momentum: MomentumFeatures
    volume: VolumeFeatures
    volatility: VolatilityFeatures
    derivatives: DerivativesFeatures
    session: SessionFeatures
    freshness: DataFreshness
    source_ids: list[str]
    missing_features: list[str]
```

```python
class RegimeScore(BaseModel):
    trend_up: float
    trend_down: float
    range: float
    compression: float
    expansion: float
    unstable: float
    evidence: dict[str, float]
```

```python
class StrategyProposal(BaseModel):
    proposal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: Literal["long", "short"]
    setup_type: str
    signal_bar_time: datetime
    expires_at: datetime
    entry_trigger: EntryTrigger
    invalidation: InvalidationRule
    targets: list[TargetRule]
    regime_fit: float
    setup_quality: float
    cost_adjusted_rr: float
    confidence_components: dict[str, float]
    feature_snapshot_hash: str
    reasons: list[str]
    risk_flags: list[str]
```

```python
class AIReview(BaseModel):
    review_id: str
    proposal_id: str
    scenario: str
    strategy_rank: int
    confidence_delta: float
    supporting_evidence_ids: list[str]
    risk_flags: list[str]
    contradiction: str | None
    expires_at: datetime
```

`confidence_delta` 必须限制为 `[-0.15, 0.15]`。

---

## 七、策略范围

只实现三种：

```text
trend_pullback_v2
failed_breakout_reversal_v1
range_sweep_reversion_v1
```

不得增加第四种策略。

### 1. trend_pullback_v2

- 趋势结构、EMA 斜率、ADX、Anchored VWAP 形成 `trend_up/down` 分数；
- 回撤到 EMA20/50、VWAP 或突破结构；
- 回撤成交量收缩；
- 重新启动 K 线确认；
- 下一确认突破或限价回踩入场；
- 结构点外加 ATR 缓冲止损；
- 分批退出和尾仓跟踪；
- 不追离结构过远的价格。

### 2. failed_breakout_reversal_v1

- 最近 Swing、Donchian 或前日高低作为唯一主边界；
- 刺破边界后收回；
- 影线、ATR、成交量或 Taker Imbalance 确认；
- 下一根确认后入场；
- 止损位于假突破极值外；
- TP1 区间中轴/1R；
- TP2 下一结构/2R；
- 支持尾仓和结构失效退出。

### 3. range_sweep_reversion_v1

- Range 分数高，趋势分数低；
- 边界至少被验证两次；
- Sweep 后重新收回；
- 成交量或 ATR 短时放大；
- 不继续创新高/低；
- 止损在 Sweep 极值外；
- TP1 区间中轴；
- TP2 对侧边界；
- 只有真实突破确认才保留尾仓。

所有策略必须：

- 仅使用闭合 K 线；
- 长空对称测试；
- 明确有效期；
- 明确结构失效；
- 计算成本后 RR；
- 不满足 RR 时返回无候选；
- 不得直接调用交易所。

---

## 八、退出重构

移除所有策略统一固定 2R 的默认执行资格。

建立每个 Proposal 独立的退出模板：

```text
TP1 部分退出
TP2 部分退出
尾仓跟踪
结构失效退出
时间退出
Regime 转换退出
硬止损
```

初始比例可使用：

```text
TP1 35%–40%
TP2 40%
尾仓 20%–25%
```

实际百分比必须版本化并进入 Trial Ledger。

止损和目标在交易所真实成交后由现有执行闭环根据真实平均成交价重新解析。策略层只输出结构和风险规则。

---

## 九、AI Market Committee

参考 TradingAgents 的角色分解，但不复制完整框架。

只实现：

```text
TechnicalContextAnalyst
StructureVolumeAnalyst
RiskCritic
MarketSynthesizer
```

必须满足：

- 输入只来自 `MarketContext` 和 `StrategyProposal`；
- 返回严格 JSON；
- 保存 Provider、Model、Prompt 版本、输入哈希、输出哈希、Token、延迟和错误；
- AI 不得改 side；
- AI 不得改 quantity；
- AI 不得生成任意最终成交价；
- AI 不得阻止硬退出；
- AI 调用失败不得伪装成功；
- AI 未调用必须记录 skip_reason。

第一阶段：

```text
AI 只生成解释和 A/B 数据，不影响执行。
```

第二阶段只有在配对 A/B 证明以下条件后才可影响排序：

```text
AI 版本相对 deterministic-only 的净期望差异 LCB > 0
PF 稳定提高
MDD 恶化不超过 2 个百分点
解析成功率 >= 99%
调用覆盖率 >= 95%
```

否则 AI 保持解释模式。

---

## 十、参数搜索限制

预先注册少量参数：

```text
structure_lookback = [24, 48, 72]
atr_buffer = [0.15, 0.25, 0.35]
volume_z_threshold = [0.5, 1.0, 1.5]
confirmation_bars = [1, 2]
```

规则：

- 每策略第一阶段最多 36 个组合；
- 开始前写 Trial Ledger；
- 记录全部组合；
- 不只保存最佳；
- 选择参数平台；
- 不查看 Final Holdout；
- 添加任何新指标前必须做消融；
- 新特征没有稳定 OOS 增益就删除。

---

## 十一、验证要求

### 成交模型

至少：

```text
NEXT_BAR_OPEN
NEXT_BAR_VWAP
QUOTE_AWARE（有历史报价时）
```

禁止信号 K 线收盘价无条件成交。

### 成本

必须包含：

```text
maker/taker fee
spread
dynamic slippage
latency
funding
partial fill
```

### 数据切分

- 冻结最近 180 天为 Final Holdout；
- 开发期间不得查看其结果；
- 外层 12 个月训练/选择，3 个月 OOS，步长 3 个月；
- Purge 覆盖最大回看和持仓；
- Embargo 至少 24 小时；
- UTC；
- BTC、ETH、组合分别报告。

### 统计

必须实现：

- Stationary 或 Moving Block Bootstrap；
- 95% 单侧净期望 LCB；
- Trial Ledger；
- Deflated Sharpe Ratio；
- CSCV/PBO；
- 参数邻域稳定性；
- Regime 分层；
- 收益集中度；
- 压力测试。

不得用 IID Bootstrap 作为最终证据。

### 压力场景

```text
fee x 1.5
slippage x 2
执行延迟 1 根 1m
不利 Funding
5% 信号漏失
部分成交
```

---

## 十二、晋级状态

```text
DRAFT
-> BASELINE_REPLAYED
-> OOS_RESEARCH_PASSED
-> FINAL_HOLDOUT_PASSED
-> TESTNET_SHADOW
-> TESTNET_FORWARD
-> ACTIVE_TESTNET
-> RETIRED
```

不得跳级。

研究缓冲门槛：

```text
胜率 >= 52%
平均盈亏比 >= 1.30
PF >= 1.65
成本后净期望 > 0
MDD <= 12%
95% 单侧净期望 LCB > 0
OOS 交易 >= 200
至少 8 个非重叠 OOS 窗口
```

最终 Testnet 门槛：

```text
胜率 >= 50%
平均盈亏比 >= 1.20
PF >= 1.50
成本后净期望 > 0
MDD <= 15%
95% 单侧净期望 LCB > 0
自然 Scheduler 连续 >= 30 天
自然已平仓交易 >= 100
```

两个 Testnet 样本条件取较晚达到者。

---

## 十三、建议文件范围

优先新增或在同职责文件中实现：

```text
services/strategy_library/context.py
services/strategy_library/regime/scorer_v2.py
services/strategy_library/candidates/trend_pullback_v2.py
services/strategy_library/candidates/failed_breakout_reversal_v1.py
services/strategy_library/candidates/range_sweep_reversion_v1.py
services/strategy_library/ensemble/selector_v2.py
services/strategy_library/exit/adaptive_exit.py
services/agents/market_committee.py
services/agents/schemas.py
services/validation/dependent_bootstrap.py
services/validation/promotion_gate_v2.py
services/validation/trial_ledger.py
```

窄修改：

```text
services/strategy_library/models.py
services/strategy_library/registry.py
services/strategy_library/runner.py
services/execution/decision_pipeline.py
services/execution/bootstrap.py
services/agents/llm_runtime.py
services/agents/service.py
services/validation/technical_replay.py
services/validation/metrics.py
services/validation/walk_forward.py
```

分支已有同职责文件时扩展现有实现，不得重复造层。

---

## 十四、TDD 执行顺序

每项严格执行：

1. 写失败测试；
2. 运行并确认因预期原因失败；
3. 最小实现；
4. 运行目标测试；
5. 运行相关模块测试；
6. 审查 diff；
7. 单独提交。

顺序：

```text
Task 0 分支审计
Task 1 Golden Baseline
Task 2 MarketContext
Task 3 RegimeScore
Task 4 failed_breakout_reversal_v1
Task 5 trend_pullback_v2
Task 6 range_sweep_reversion_v1
Task 7 adaptive_exit
Task 8 selector_v2
Task 9 AI invocation observability
Task 10 AI Market Committee explanation mode
Task 11 dependent bootstrap / trial ledger / promotion gate
Task 12 walk-forward / holdout / stress
Task 13 AI paired A/B
Task 14 Testnet Shadow
Task 15 Testnet Forward
```

每个任务结束必须汇报：

```text
目标:
修改文件:
新增测试:
失败测试证据:
通过测试证据:
未解决阻塞:
是否扩大范围:
commit:
```

---

## 十五、关键测试

至少实现：

```text
test_4h_disagreement_reduces_score_but_does_not_hard_veto
test_market_context_uses_only_point_in_time_closed_bars
test_missing_funding_is_not_replaced_with_zero
test_failed_breakout_short_matches_sweep_reclaim_pattern
test_failed_breakout_rejects_true_breakout_continuation
test_range_sweep_rejects_unreclaimed_breakdown
test_trend_pullback_rejects_overextended_entry
test_proposal_expires_after_configured_bars
test_trade_plan_rejects_excessive_price_drift
test_adaptive_exit_creates_partial_targets
test_selector_never_averages_opposing_directions
test_ai_cannot_change_side_quantity_or_hard_stop
test_ai_skip_and_token_usage_are_persisted
test_runtime_and_replay_emit_identical_proposals
test_optimizer_cannot_read_final_holdout
test_all_parameter_trials_are_persisted
test_stationary_bootstrap_preserves_trade_clusters
test_promotion_fails_when_expectancy_lcb_is_not_positive
test_promotion_fails_when_win_rate_is_below_50_percent
test_promotion_fails_when_avg_win_loss_is_below_1_20
test_promotion_fails_when_profit_factor_is_below_1_50
test_promotion_fails_when_drawdown_exceeds_15_percent
```

---

## 十六、停止条件

出现以下任一情况，停止继续扩展并汇报：

- 三个策略均无法在冻结 OOS 达到正净期望；
- 达标仅来自单个参数点；
- Final Holdout 失败；
- 置信区间下界不高于 0；
- 收益主要来自一个季度；
- 成本翻倍后策略立即崩溃；
- 回测与运行时 Proposal 不一致；
- AI A/B 没有证明增益；
- 需要修改执行状态机才能让策略“看起来通过”；
- 需要删除亏损交易或调整数据切分；
- 已尝试三次根因修复仍出现不同问题。

停止时输出根因和最小下一步，不得继续无限修改。

---

## 十七、最终验证

完成前必须新鲜运行：

```bash
pytest -q
ruff check .
ruff format --check .
mypy services apps
python scripts/sync_skill_copies.py --check
python scripts/refresh_current_state.py --run --check
```

以及仓库已有的前端、Hook、Manifest、证据包和集成检查。

不得只报告测试数量。必须提供：

- 完整命令；
- Exit Code；
- Pass/Fail/Skip 数；
- Git SHA；
- 未运行项及原因；
- OOS 报告；
- Final Holdout 报告；
- Testnet Forward 报告；
- 最终状态 `ACTIVE_TESTNET` 或 `NO_ACTIVE_STRATEGY`。

---

## 十八、输出原则

禁止说：

```text
应该可以
大概率盈利
看起来已经完成
Sharpe 很高所以可用
测试很多所以可用
```

只能基于最新运行证据下结论。

开始执行时，先完成 Task 0 和 Task 1，只提交审计和黄金基线结果，等待审核后再进入策略代码修改。
