# 加密货币量化策略收口重构方案

**日期：** 2026-07-29
**目标分支：** `fix/v2-production-closure`
**目标市场：** Binance USDT-M Futures Testnet，初期仅 BTCUSDT、ETHUSDT
**变更原则：** 策略内核大改；数据、执行、数据库和 API 合同尽量不动；不再继续在旧规则上打补丁。

---

## 0. 结论先行

本轮不采用以下方案：

1. 继续降低原有 4h/1h/15m 漏斗阈值；
2. 把更多指标堆进同一个策略；
3. 让 LLM 直接自由生成入场价、止损价、止盈价和下单数量；
4. 一次性迁移到 Freqtrade、Lean、NautilusTrader、vn.py 或其他完整框架；
5. 使用大规模 Hyperopt、遗传算法或强化学习强行“搜出”达标结果；
6. 用 Testnet Sampling 单据作为盈利证据。

本轮采用：

> **多策略确定性候选 + 概率型市场状态 + AI 场景评审 + 程序化交易计划 + 严格统计晋级。**

保留现有 `services/strategy_library`、`services/validation` 和 `services/execution` 的边界；在 `StrategyCandidate/TradeIntent` 之前替换策略内核，在其之后保持执行层稳定。

---

# 1. 不可承诺事项与可交付事项

没有任何工程方案可以保证市场中一定存在同时满足下列条件的策略：

- 胜率 ≥ 50%
- 平均盈利 ÷ 平均亏损 ≥ 1.20
- Profit Factor ≥ 1.50
- 手续费、滑点和资金费率后的单笔净期望 > 0
- 最大回撤 ≤ 15%
- 净期望置信区间下界 > 0

本方案能保证的是：

1. 指标按同一套定义计算；
2. 不通过的策略绝不被激活；
3. 不通过时系统明确返回“没有合格策略”，而不是继续调参伪造结果；
4. 所有候选、参数尝试、数据切分和淘汰原因均可追踪；
5. 研究、回放、Testnet 使用同一策略逻辑和时间语义；
6. AI 是否真正提高策略表现通过 A/B 证据决定，而不是凭主观判断。

---

# 2. 指标的统一定义

## 2.1 联合约束

设：

- `p`：胜率；
- `r`：平均盈利 ÷ 平均亏损；
- `PF`：Profit Factor。

则：

```text
PF = p × r / (1 - p)
```

因此：

- `p = 50%`、`r = 1.20` 时，`PF = 1.20`；
- `r = 1.20` 时，要达到 `PF = 1.50`，胜率至少约为 `55.56%`；
- `p = 50%` 时，要达到 `PF = 1.50`，平均盈亏比至少为 `1.50`。

不得将三个门槛分开调优。策略选择目标是联合满足，而不是分别擦线。

## 2.2 统计口径

所有指标必须使用扣除以下成本后的已平仓交易：

- 实际或配置化 maker/taker 手续费；
- 动态滑点；
- 历史资金费率；
- 部分成交与剩余仓位；
- 入场、加减仓和多级退出的所有成本。

定义：

```text
win_rate =
    net_pnl_after_costs > 0 的已平仓交易数
    / 全部已平仓交易数

avg_win_loss_ratio =
    mean(正的 net_pnl_after_costs)
    / abs(mean(负的 net_pnl_after_costs))

profit_factor =
    sum(正的 net_pnl_after_costs)
    / abs(sum(负的 net_pnl_after_costs))

net_expectancy =
    mean(net_return_in_R_after_costs)

max_drawdown =
    按时间顺序、组合级、逐时盯市净值曲线的最大回撤
```

最终置信度要求：

```text
95% 单侧净期望置信区间下界 > 0
```

置信区间不得使用假设交易独立同分布的普通 IID Bootstrap；应使用 Stationary Bootstrap 或 Moving Block Bootstrap，保留市场状态和连续盈亏的时间相关性。

---

# 3. 两层晋级门槛

为了避免刚好擦线、上线后轻微衰减便失败，研究阶段使用更高的缓冲门槛。

## 3.1 研究晋级缓冲门槛

建议：

- 胜率 ≥ 52%
- 平均盈亏比 ≥ 1.30
- Profit Factor ≥ 1.65
- 成本后净期望 > 0
- 最大回撤 ≤ 12%
- 95% 单侧净期望 LCB > 0
- OOS 已平仓交易数 ≥ 200
- 至少 8 个非重叠 OOS 窗口
- 不得由单个季度贡献超过 40% 的总利润
- 不得由单一标的贡献超过 70% 的总利润

## 3.2 最终 Testnet Forward 门槛

用户硬门槛：

- 胜率 ≥ 50%
- 平均盈亏比 ≥ 1.20
- Profit Factor ≥ 1.50
- 成本后单笔净期望 > 0
- 最大回撤 ≤ 15%
- 95% 单侧净期望 LCB > 0

额外证据量：

- 至少连续 30 天；
- 至少 100 笔自然 Scheduler 已平仓交易；
- 两个条件取较晚达到者；
- 不允许 Acceptance 脚本或人工往返单计入。

如果没有策略通过，正确结果是保持 `NO_ACTIVE_STRATEGY`。

---

# 4. 开源项目取舍矩阵

## 4.1 TradingAgents / TradingAgents-CN

借鉴：

- 技术、市场结构、情绪/新闻、风险等专业角色；
- Bull/Bear 批判，而不是单模型一次输出；
- Research Manager / Portfolio Manager 结构化汇总；
- 决策日志、检查点恢复；
- 多 Provider 适配；
- 工具和真实数据快照约束。

不采用：

- LLM 直接决定交易数值；
- 多轮无限辩论；
- 将研究框架的输出当作已验证 Alpha；
- 复制 TradingAgents-CN 中许可边界不明确或受限的前端代码。

本项目收口版本只设 4 个实时角色：

1. `TechnicalContextAnalyst`
2. `StructureVolumeAnalyst`
3. `RiskCritic`
4. `MarketSynthesizer`

最多一轮并行分析和一次汇总。

## 4.2 Freqtrade

借鉴：

- 简洁、独立的 Strategy Interface；
- Backtest、Dry-run、Live 使用同一策略代码；
- Lookahead Analysis；
- Recursive Analysis；
- Strategy Callback；
- Protections；
- 参数空间显式隔离；
- 结果可视化和交易级分析。

不采用：

- 整体迁移；
- 无边界 Hyperopt；
- 直接复制社区策略；
- 在没有试验账本的情况下搜大量指标参数。

## 4.3 Hummingbot

借鉴：

- Strategy/Controller 与 Executor/Connector 分离；
- 每个执行动作有独立生命周期；
- 连接器、订单和状态管理边界。

不采用：

- 高频做市、跨交易所套利和复杂库存模型；
- 为 15 分钟方向策略引入完整 HFT 架构。

## 4.4 QuantConnect Lean

借鉴：

- Fill、Fee、Slippage、Brokerage Model 可插拔；
- 部分成交、交易规则、订单有效性验证；
- 研究现实模型和生产现实模型一致。

不采用：

- C# 引擎迁移；
- 复制完整证券抽象。

## 4.5 Superalgos

借鉴：

- 对每次决策做可视化回放；
- 在 K 线上显示触发条件、否决条件、入场、止损、分批退出。

不采用：

- 完整可视化节点编辑器；
- 平台级迁移。

## 4.6 Vibe-Trading

重点借鉴：

- Hypothesis Registry；
- 不可变 Run Card；
- 工具调用轨迹；
- Point-in-time 数据；
- Bootstrap、Monte Carlo、Walk-forward；
- 研究假设 → 信号 → 回测 → 证据的闭环；
- 多数据源回退时明确显示来源和失败原因。

这是最适合当前项目研究治理层的参考。

## 4.7 Qbot / vn.py

借鉴：

- 数据、因子、模型、策略、回测、模拟、交易分层；
- 事件驱动；
- 统一模型 API；
- 因子处理和缺失值语义；
- 交易前风险管理；
- 数据记录器。

不采用：

- 旧框架整体集成；
- 在本轮引入 RL；
- 重建现有执行网关。

## 4.8 abu

借鉴：

- 基础策略与独立监督/拦截层分离；
- 将 MetaLabel 看成独立“裁判”，而不是策略内部又一组条件。

不采用：

- 直接复制代码；
- 过时依赖；
- 大量技术形态无差别引入。

## 4.9 daily_stock_analysis

借鉴：

- Provider Routing；
- Context Pack；
- 实时任务进度；
- 数据源、缺失、降级原因显式显示；
- AI 调用、Token、错误可观测。

不采用：

- 股票基本面策略；
- 自然语言买卖点直接进入执行。

## 4.10 新增参考：Jesse

借鉴：

- 简洁 Strategy API；
- 多周期、多标的且避免前视；
- 部分退出；
- Monte Carlo；
- 特征和标签采集；
- 研究到 Live 的一致性。

## 4.11 新增参考：NautilusTrader

重点借鉴：

- 确定性事件时钟；
- 研究和 Live 共享时间语义；
- 同一策略代码部署；
- 订单、事件、状态可重放；
- ReduceOnly、OCO 等订单语义。

不采用：

- Rust 核心迁移；
- 重做当前执行引擎。

## 4.12 新增参考：FinRL-X

借鉴：

- 规则策略、ML、LLM 信号都通过统一策略协议；
- AI 组件不得改变下游执行语义；
- 研究和部署保持一致。

本轮明确不采用端到端强化学习。原因是样本、奖励函数、交易摩擦和非平稳性会显著放大过拟合风险。

---

# 5. 当前策略模块的处置

## 5.1 保留

保留现有边界和能力：

- `services/strategy_library/models.py`
- `services/strategy_library/registry.py`
- `services/strategy_library/runner.py`
- `services/strategy_library/candidates/`
- `services/strategy_library/regime/`
- `services/strategy_library/ensemble/`
- `services/strategy_library/exit/`
- `services/validation/admission.py`
- `services/validation/costs.py`
- `services/validation/metrics.py`
- `services/validation/stress_scenarios.py`
- `services/validation/walk_forward.py`
- `services/validation/technical_replay.py`
- 现有 `DecisionPipeline -> TradeIntent` 执行合同
- Active Manifest 和证据包概念

实际分支中名称若变化，沿用已有命名，不建立重复平行层。

## 5.2 停用或降级为基准

以下内容不得继续作为默认自动策略：

1. 全局硬编码的 `4h_direction_15m_entry`；
2. 4h/1h 不一致便绝对否决的规则；
3. 所有策略统一固定 2R；
4. `operator_heuristic_v1` 自动执行；
5. 未验证的 1d/4h Swing 自动启用；
6. Manifest 异常后回退到默认可执行策略；
7. `signal_observation` 复制正式策略并绕过 Edge Gate；
8. AI 二元 veto；
9. 只有 MACD/RSI 交叉，没有结构和成本约束的候选；
10. 重复或占位候选。

旧策略移入：

```text
services/strategy_library/legacy/
```

或保留原位置但在 Registry 标记：

```text
status = BASELINE_ONLY
execution_eligible = false
```

不可删除历史回测结果，以便做基准对比。

## 5.3 只保留三种首发策略族

第一轮不得增加第四种策略，除非三者全部完成验证后仍没有足够候选。

1. `trend_pullback_v2`
2. `failed_breakout_reversal_v1`
3. `range_sweep_reversion_v1`

后续候选：

4. `volatility_compression_breakout_v1`

---

# 6. 新的稳定合同

## 6.1 MarketContext

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

规则：

- 信号只使用闭合 K 线；
- 1m/5m 只用于确认和执行新鲜度；
- 15m 是第一阶段主要信号周期；
- 1h/4h 是软上下文；
- 缺失 Funding/OI 不得用 0 冒充；
- 没有可靠历史订单簿数据，不得在回测中使用盘口特征。

## 6.2 RegimeScore

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

所有值在 `[0, 1]`。

Regime 只影响策略适配评分，不作为全局绝对 Gate。只有 `unstable`、数据过期或风险状态可以硬阻止 Entry。

## 6.3 StrategyProposal

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

策略输出结构和风险距离，不输出可直接信任的最终成交价。

## 6.4 AIReview

```python
class AIReview(BaseModel):
    review_id: str
    proposal_id: str

    scenario: str
    strategy_rank: int
    confidence_delta: float  # 限制在 [-0.15, 0.15]
    supporting_evidence_ids: list[str]
    risk_flags: list[str]
    contradiction: str | None
    expires_at: datetime
```

AI 不得：

- 改变订单数量；
- 自由创造新价格；
- 把空单改成多单；
- 放宽硬风险规则；
- 阻止硬止损；
- 把缺失数据当作事实。

## 6.5 TradePlan

```python
class TradePlan(BaseModel):
    proposal_id: str
    symbol: str
    side: Literal["long", "short"]

    entry_type: Literal[
        "market_after_confirmation",
        "stop_confirmation",
        "limit_retest",
    ]
    reference_price: Decimal
    max_price_drift_bps: Decimal

    stop_rule: ResolvedStopRule
    take_profit_ladder: list[ResolvedTarget]
    time_exit: TimeExitRule | None
    trailing_rule: TrailingRule | None

    expected_cost_bps: Decimal
    expected_reward_r: Decimal
    expected_loss_r: Decimal
    cost_adjusted_rr: Decimal
```

最终价格在下单前使用最新市场快照解析；保护价格在真实成交后基于平均成交价重新计算。

---

# 7. 首发策略定义

## 7.1 trend_pullback_v2

目标：在已有趋势中等待回撤和重新启动，不追涨杀跌。

### Regime 适配

- 结构呈 HH/HL 或 LH/LL；
- EMA 斜率、ADX、价格相对 Anchored VWAP 作为评分；
- 1h/4h 一致时加分，不一致时降分而不是直接拒绝；
- 高波动失序时拒绝。

### 多头 Setup

1. `trend_up` 分数达到候选阈值；
2. 15m 价格回撤至 EMA20/EMA50、Anchored VWAP 或最近突破结构附近；
3. 回撤期间成交量收缩；
4. 出现重新站回、吞没、Pin Bar 或局部高点突破确认；
5. 预估成本后盈亏比达到策略最低值。

空头对称。

### Entry

- 确认 K 线高点/低点突破；
- 或限价回踩确认区；
- 信号有效期不超过 2 根 15m K 线；
- 执行前检查最新价格漂移。

### Stop

- 最近有效结构点外；
- 加 `0.20–0.35 ATR` 缓冲；
- 最小和最大 ATR 距离受限。

### Exit

建议初始模板：

- TP1：1R，平 35%；
- TP2：1.8R，平 40%；
- 剩余 25% 使用 Chandelier/结构跟踪；
- TP1 后止损不自动机械移动到开仓价，只有结构允许时调整；
- 8 根 15m K 线内未达到 0.5R，可时间退出。

## 7.2 failed_breakout_reversal_v1

目标：量化用户描述的“突破无力、收回区间、反向入场”。

### 阻力/支撑定义

候选边界来自：

- 最近确认 Swing High/Low；
- Donchian 24/48；
- 局部成交量密集区边界；
- 前日高低点。

不得一次混合十种水平；每个 Proposal 必须标记唯一主要边界来源。

### 做空 Setup

1. High 突破阻力；
2. Close 重新回到阻力下方；
3. 上影线占 K 线范围达到阈值；
4. 突破距离位于合理 ATR 区间；
5. 成交量或 Taker Buy 放大，但价格没有延续；
6. 下一根 K 线跌破信号 K 线低点或弱反弹失败。

做多对称。

### Stop

- 假突破极值外 `0.15–0.30 ATR`；
- 基于真实成交价重新验证风险距离；
- 止损距离过大则直接跳过，不缩小到不合理位置。

### Exit

- TP1：区间中轴或 1R，取更近者，平 40%；
- TP2：下一结构支撑/阻力或 2R，平 40%；
- 尾仓 20% 在动能延续时跟踪；
- 到达支撑后成交量衰减、波动压缩时允许提前部分退出；
- 重新站回假突破失效区时提前退出。

## 7.3 range_sweep_reversion_v1

目标：量化“盘整蓄力、刺破前低/前高后收回”的流动性扫损。

### Regime

- `range` 分数高；
- 趋势分数低；
- 区间边界至少被验证两次；
- 区间宽度相对 ATR 合理；
- 不在高影响事件或异常扩张中。

### 多头 Setup

1. Low 刺破区间下沿或确认前低；
2. Close 收回区间；
3. 下影线明显；
4. 成交量/ATR 短时放大；
5. 后续没有继续创新低；
6. 重新站回区间下沿或突破反转 K 线高点。

空头对称。

### Stop

- Sweep 极值下方或上方；
- 加 ATR/tick 缓冲。

### Exit

- TP1：区间中轴，平 40%；
- TP2：区间另一侧，平 40%；
- 尾仓 20% 只在突破区间并有成交量确认时保留；
- 区间结构消失时提前退出。

---

# 8. 参数搜索收口

每个策略只允许预先声明少量参数：

```text
structure_lookback: [24, 48, 72]
atr_buffer: [0.15, 0.25, 0.35]
volume_z_threshold: [0.5, 1.0, 1.5]
confirmation_bars: [1, 2]
```

约束：

- 每个策略第一阶段最多 36 个明确组合；
- 参数搜索前生成 Trial Ledger；
- 所有失败组合也保留；
- 选择稳定平台，不选择单点最高；
- 不允许 Agent 看到最终 Holdout 后继续调整；
- 不允许无限增加新指标；
- 每增加一个特征，必须完成消融并证明 OOS 增益。

---

# 9. AI 的真实接入方案

## 9.1 离线 Research Agent

职责：

1. 读取 Strategy Source Registry；
2. 汇总论文和开源实现思想；
3. 提取策略假设；
4. 生成可测试的 `HypothesisSpec`；
5. 触发确定性回测；
6. 写入 Run Card；
7. 根据证据接受、修改或淘汰。

每条来源保存：

```text
repo_url
commit_sha_or_release
license_review
strategy_family
claimed_mechanism
applicable_market
required_data
known_limitations
implementation_decision
```

只借鉴思想，不复制许可不兼容代码。

## 9.2 实时 Market Committee

输入只允许使用 point-in-time `MarketContext` 和现有 `StrategyProposal`。

角色：

### TechnicalContextAnalyst

分析趋势、动量、波动率和多周期上下文。

### StructureVolumeAnalyst

分析结构、假突破、流动性扫损、成交量和订单流。

### RiskCritic

主动寻找反例：

- 数据是否旧；
- 成本是否吞噬收益；
- 是否接近事件；
- 是否存在相反结构；
- 止损是否过宽；
- 策略是否与 Regime 不匹配。

### MarketSynthesizer

输出严格 `AIReview` JSON。

## 9.3 AI A/B 规则

同时回放：

- A：确定性策略；
- B：确定性策略 + AI 排序/置信度调整。

AI 只有满足以下条件才可影响 Testnet Entry：

- 相同 Proposal 样本上的配对净期望差异 LCB > 0；
- PF 有稳定改善；
- MDD 不恶化超过 2 个百分点；
- JSON 解析成功率 ≥ 99%；
- 调用成功覆盖率 ≥ 95%；
- 无前视信息；
- AI 版本、Prompt、模型和输入哈希固定。

不通过时：

- AI 继续生成解释；
- `confidence_delta` 不参与执行；
- API 仍有真实调用和可观测 Token；
- 不允许为了“用上 AI”而强行影响交易。

---

# 10. 回测与现实模型

## 10.1 同一策略代码

运行时和回放必须调用同一：

```text
MarketContextBuilder
RegimeScorer
Strategy.generate_proposal()
CandidateSelector
TradePlanBuilder
ExitPolicy
```

回测不得重写一套简化信号。

## 10.2 成交模型

至少并行输出：

1. `NEXT_BAR_OPEN`
2. `NEXT_BAR_VWAP`
3. `QUOTE_AWARE`（有可靠历史报价时）

不得以信号 K 线收盘价无条件成交。

## 10.3 成本模型

```text
entry_fee
exit_fee
spread_cost
market_impact
latency_slippage
funding
partial_fill_cost
```

基础结果使用真实账户配置或明确版本化配置。

压力场景：

- 手续费 × 1.5；
- 滑点 × 2；
- 延迟一根 1m K 线；
- Funding 使用历史不利分位数；
- 5% 信号随机漏失；
- 订单部分成交。

## 10.4 数据划分

推荐：

- 最终 Holdout：冻结时点前最近 180 天，开发期间不可读取结果；
- 外层 Walk-forward：12 个月训练/选择，3 个月测试，步长 3 个月；
- Purge：至少覆盖最大特征回看和最大持仓周期；
- Embargo：至少 24 小时；
- BTC、ETH 独立结果和组合结果同时报告；
- 所有时区统一 UTC。

## 10.5 选择偏差控制

加入：

- Trial Ledger；
- Deflated Sharpe Ratio；
- CSCV/PBO；
- White Reality Check 或 SPA；
- Stationary Bootstrap；
- 参数邻域稳定性；
- Regime 分层；
- 周/月收益贡献集中度。

---

# 11. 策略晋级状态机

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

禁止跳级。

`TESTNET_SHADOW`：

- 产生完整 TradePlan；
- 不提交订单；
- 记录假设成交和真实后续结果。

`TESTNET_FORWARD`：

- 真实 Binance Testnet 自然 Scheduler 下单；
- 交易必须来自策略，而非 Acceptance；
- 计入最终 Forward 门槛。

---

# 12. 对当前仓库的最小变更面

## 12.1 建议新增

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

分支中若已有相同职责文件，必须扩展现有文件，不得建立重复实现。

## 12.2 只做窄修改

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

## 12.3 本轮禁止修改

除非是适配稳定接口且有明确失败测试：

```text
services/execution/paper_exchange_execution.py
services/execution/paper_order_lifecycle.py
交易所 Gateway
前端
数据库执行状态机
```

这些正在进行生产闭环修复。策略重构不得与其并行抢改同一逻辑。

---

# 13. 实施阶段

## Phase 0：分支审计和冻结

先运行：

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git diff --stat main...HEAD
git diff --name-only main...HEAD
```

输出：

- 当前策略完整调用链；
- 当前执行合同；
- 当前分支已修改文件；
- 与生产闭环任务冲突的文件；
- 当前测试基线；
- 当前活跃策略和 Manifest；
- 当前 AI 调用短路条件。

未完成前禁止改代码。

## Phase 1：黄金基线

建立固定数据切片和 Golden Replay：

- BTC/ETH；
- 至少包含趋势、区间、假突破、波动扩张；
- 保存旧策略所有指标；
- 保存逐单交易；
- 保存决策漏斗。

目标不是优化，而是确保新旧可比较。

## Phase 2：MarketContext 和 RegimeScore

只实现：

- 数据合同；
- 特征；
- 时间语义；
- 缺失值；
- 概率型 Regime。

不得加入 Entry。

## Phase 3：三策略独立实现

顺序：

1. failed_breakout_reversal_v1
2. trend_pullback_v2
3. range_sweep_reversion_v1

每个策略独立：

- 单元测试；
- 合成场景测试；
- Golden Replay；
- OOS 报告；
- 消融。

不得边实现边改另一个策略。

## Phase 4：动态退出

替换统一 2R：

- 分批退出；
- 结构目标；
- 时间退出；
- 跟踪退出；
- 成本后 RR 检查。

## Phase 5：Selector

规则：

- 相反方向 Proposal 不平均；
- 同一标的只选择一个主 Setup；
- Regime Fit、Setup Quality、Cost-adjusted RR、冲突惩罚组成分数；
- 多策略同时有效时选择最强，而不是叠加仓位；
- 低于最低分则不交易。

## Phase 6：AI Market Committee

先接通真实调用和记录，再做 A/B。

第一步：

- AI 只解释；
- Token 非 0；
- 失败和跳过原因可见。

第二步：

- 完成历史 A/B；
- 证明确有增益后才允许影响排序。

## Phase 7：统计晋级

完成：

- Walk-forward；
- Final Holdout；
- Stationary Bootstrap；
- DSR；
- PBO；
- Stress；
- Promotion Manifest。

## Phase 8：Testnet Forward

必须使用自然 Scheduler：

- 信号；
- AI；
- TradePlan；
- Binance 成交；
- 分批 ReduceOnly；
- 本地投影；
- 最终对账。

---

# 14. 验收测试目录建议

```text
tests/services/strategy_library/test_market_context.py
tests/services/strategy_library/test_regime_scorer_v2.py
tests/services/strategy_library/test_trend_pullback_v2.py
tests/services/strategy_library/test_failed_breakout_reversal_v1.py
tests/services/strategy_library/test_range_sweep_reversion_v1.py
tests/services/strategy_library/test_selector_v2.py
tests/services/strategy_library/test_adaptive_exit.py

tests/services/agents/test_market_committee.py
tests/services/agents/test_ai_ablation.py

tests/services/validation/test_dependent_bootstrap.py
tests/services/validation/test_trial_ledger.py
tests/services/validation/test_promotion_gate_v2.py
tests/services/validation/test_no_lookahead.py
tests/services/validation/test_cost_model.py
tests/services/validation/test_walk_forward_refit.py

tests/integration/test_strategy_runtime_parity.py
tests/integration/test_testnet_strategy_natural_cycle.py
```

关键测试：

```text
test_4h_disagreement_reduces_score_but_does_not_hard_veto
test_failed_breakout_short_uses_closed_bars_only
test_failed_breakout_stop_is_beyond_sweep_extreme
test_range_sweep_reclaim_rejects_continuing_breakdown
test_trade_plan_expires_when_market_moves_away
test_exit_ladder_uses_confirmed_fill_price
test_ai_cannot_change_side_or_order_quantity
test_ai_failure_does_not_block_hard_exit
test_backtest_and_runtime_generate_same_proposal
test_final_holdout_is_not_available_to_optimizer
test_all_trials_are_recorded
test_promotion_fails_when_expectancy_lcb_is_non_positive
test_promotion_fails_when_pf_is_below_1_50
test_promotion_fails_when_mdd_exceeds_15_percent
```

---

# 15. 绝对禁止项

- 禁止承诺一定达标；
- 禁止为了达标删除亏损交易；
- 禁止使用未来 K 线；
- 禁止以当前新闻回填历史日期；
- 禁止普通 IID Bootstrap 作为最终 CI；
- 禁止反复查看 Holdout；
- 禁止增加大量参数后只报告最佳结果；
- 禁止 AI 自由输出成交价；
- 禁止用旧 K 线收盘价建立实时仓位；
- 禁止把 Testnet Sampling 计入策略证据；
- 禁止在策略重构中重写交易所状态机；
- 禁止项目级架构重构；
- 禁止无测试删除旧策略；
- 禁止同时改策略、执行、前端。

---

# 16. 最终交付物

1. `strategy_source_registry.json`
2. `strategy_hypotheses.json`
3. 旧策略 Golden Baseline
4. 三个策略实现和独立报告
5. Deterministic-only 与 AI-assisted A/B 报告
6. Trial Ledger
7. Walk-forward 报告
8. Final Holdout 报告
9. Stress 报告
10. Promotion Manifest
11. Testnet Forward 交易明细
12. 一份 `NO_ACTIVE_STRATEGY` 或 `ACTIVE_TESTNET` 的最终结论
13. 所有测试和验证命令的原始输出

最终系统只允许两种诚实状态：

```text
ACTIVE_TESTNET:
    证据全部满足门槛

NO_ACTIVE_STRATEGY:
    当前没有可信达标策略
```

不允许“接近达标，所以先开起来”。
