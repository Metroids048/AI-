# 加密货币量化策略系统 V2 重构总方案

> 日期：2026-07-27
> 项目：Metroids048/AI-
> 范围：BTCUSDT、ETHUSDT，Binance USDT-M Testnet；Mainnet 继续关闭
> 前置依赖：交易执行恢复方案中的 Exchange-First、成交凭证、对账 fail-closed、ReduceOnly 退出安全必须先完成

---

## 0. 结论先行

本次不是继续修补 `4h → 1h → 15m → 固定 2R`，而是将现有策略层改造成：

```text
实时、点时一致的数据
    ↓
统一特征库
    ↓
市场状态识别
    ↓
多个互相独立的策略族产生候选
    ↓
确定性候选评分和冲突消解
    ↓
AI 市场委员会进行有边界的场景分析
    ↓
程序基于实时盘口和实际成交价生成执行计划
    ↓
Binance Testnet Exchange-First 下单
    ↓
策略特定的分批止盈、失效退出和跟踪退出
    ↓
归因、复盘和策略淘汰
```

### 本次重构的核心取舍

1. 不让 AI 凭自然语言自由报入场价、止损价或任意下单。
2. 不再让 4h/1h 成为所有策略的绝对否决门。
3. 不再使用所有策略共用的固定 2R 退出。
4. 不再把多个相似指标包装成多个“不同策略”。
5. 不直接复制 GitHub 上声称盈利的策略。
6. 策略、回测、实时运行共用同一份特征与信号代码。
7. 旧策略先进入 Shadow，达到迁移门槛后再删除，避免无回滚的大爆炸重写。
8. 胜率不是唯一目标，真正优化目标是成本后的样本外净期望。

---

# 1. 目标函数和不可承诺的边界

## 1.1 用户目标的数学化

“胜率和盈亏比尽量高于 50%”需要转成可测试指标：

- OOS 胜率目标：`WinRate >= 50%`
- 平均盈亏比：`AvgWin / AvgLoss >= 1.20`
- Profit Factor：`GrossProfit / GrossLoss >= 1.50`
- 成本后单笔期望：`NetExpectancy > 0`
- 最大回撤：`MaxDrawdown <= 15%`
- OOS 交易数量：正式晋升时累计不少于 200 笔，并覆盖主要市场状态
- 90% Bootstrap 置信区间：净期望下界最终必须高于 0
- 任何一个月或单一市场状态贡献的利润不得超过总利润的 40%

期望公式：

```text
NetExpectancy
= WinRate × AvgWin
- (1 - WinRate) × AvgLoss
- Fees
- Slippage
- Funding
```

胜率超过 50% 但平均盈利远小于平均亏损仍可能亏钱；低于 50% 的策略也可能盈利。本项目可以将“胜率至少 50%”作为你的业务偏好和晋升约束，但不能承诺未来每个市场阶段都保持超过 50%。

## 1.2 多目标优化函数

优化器不直接最大化胜率，避免形成“经常小赚、偶尔巨亏”的策略：

```text
Objective =
    OOS_NetExpectancy
    + 0.35 × OOS_Sharpe
    + 0.20 × Calmar
    - 0.50 × MaxDrawdown
    - 0.20 × TurnoverPenalty
    - 0.30 × ParameterInstability
    - 0.30 × RegimeConcentration
```

硬约束：

```text
WinRate >= 0.50
AvgWinLossRatio >= 1.20
ProfitFactor >= 1.50
TradeCount >= 当前阶段最低样本
NoLookahead == True
ExecutionParity == True
```

---

# 2. 当前策略层的诊断

当前项目拥有：

- `services/strategy_library/candidates`
- `services/strategy_library/entry`
- `services/strategy_library/exit`
- `services/strategy_library/regime`
- `services/strategy_library/technical`
- `services/strategy_library/ensemble`
- `services/strategy_library/models.py`
- `services/strategy_library/registry.py`
- `services/strategy_library/runner.py`
- `services/strategy_library/playbook.py`
- `services/strategy_library/meta_label_model.py`

现有方向性链路主要是：

```text
4h 方向
→ 1h 状态
→ 15m 入场
→ SignalEnsemble
→ MetaLabel
→ OOS Manifest
→ Gatekeeper
```

现有主要候选：

- `operator_heuristic_v1`
- `trend_momentum_v1`
- `trend_breakout_v1`

主要结构问题：

1. 高周期作为绝对门槛，导致反转、区间和假突破策略无法工作。
2. `trend_momentum_v1` 和 `trend_breakout_v1` 仍高度共享趋势、ADX 等信息，策略多样性有限。
3. 所有候选共用固定 2R 退出，忽略策略的不同收益分布。
4. 指标条件多，但缺少市场结构、成交量、订单流和衍生品信息。
5. MetaLabel 在标签样本不足时容易成为一个不透明的额外拒绝器。
6. AI 只是隐藏在多重开关后的可选 veto，没有构成真实分析链。
7. 回放和实时执行存在价格、时间和成交语义偏差。
8. “没有交易”无法区分是无信号、状态不匹配、MetaLabel、风险、AI、数据过期还是执行失败。

---

# 3. 外部开源项目采纳矩阵

## 3.1 第一优先级：直接指导本项目设计

### A. NautilusTrader

仓库：
https://github.com/nautechsystems/nautilus_trader

采纳：

- 研究和实时交易采用相同事件语义和时间模型
- 类型化 Order/Fill/Position 事件
- 确定性事件驱动回放
- ReduceOnly、OCO、OTO 等订单模型
- Client Order ID 和 Venue Order ID 双身份
- 订单簿、成交、Bar、自定义数据统一进入事件总线
- 回测与实时的同代码策略模式

不采纳：

- 当前阶段不把整个项目迁移到 Rust/Nautilus
- 不进行一次性执行引擎替换
- 只借鉴领域模型、时钟、事件和状态机设计

理由：

全面迁移会放大风险并推翻正在进行的执行修复。V2 应先在现有 Python 架构中实现研究—实时一致性，后续再评估迁移价值。

### B. Freqtrade

仓库：
https://github.com/freqtrade/freqtrade

采纳：

- Dry-run 优先
- `lookahead-analysis`
- `recursive-analysis`
- Backtesting / Hyperopt / Edge 分工
- 明确的 entry/exit tag
- FreqAI 的训练、预测和特征管线分离
- 交易与策略可观测 UI
- Futures 模式的测试思路

不采纳：

- 不直接复制其策略仓库中的策略
- 不直接嵌入 GPL 代码，除非整个项目许可证已评估
- 不照搬 Hyperopt 后的“最佳参数”作为生产参数

### C. Jesse

仓库：
https://github.com/jesse-ai/jesse

采纳：

- 多周期、多标的无前视偏差
- 策略接口简化
- 部分成交和多阶段订单管理
- Monte Carlo 交易顺序重排
- K 线扰动压力测试
- Optuna 和交叉验证的优化工作流
- ML 概率模型校准

不采纳：

- 不迁移整个框架
- 不因框架提供指标而大量堆叠指标

### D. Cryptofeed

仓库：
https://github.com/bmoscon/cryptofeed

采纳：

- 标准化的 Trades、L1/L2 Book、Funding、Open Interest、Liquidations、Candles 数据合同
- Binance Futures 公共流和认证流分离
- 每条事件带 exchange timestamp、receive timestamp 和 sequence
- 订单簿校验、重连和数据缺口状态

实现选择：

当前只有 Binance，可先继续使用官方 Binance Stream，但数据模型按 Cryptofeed 的标准化思路设计。等增加第二交易所时，再决定是否真正引入依赖。

## 3.2 第二优先级：Agent、研究和治理

### E. TradingAgents

仓库：
https://github.com/TauricResearch/TradingAgents

采纳：

- 专业角色分工
- 结构化输出
- 多 Provider Registry
- Checkpoint 恢复
- 持久决策日志
- Bull/Bear 研究辩论
- Risk Manager 和 Portfolio Manager 最终汇总
- 每个 Agent 的调用和错误可观察

不采纳：

- 股票基本面角色不直接用于 BTC/ETH
- 不把模拟交易结果当作自动执行证据
- 不让非确定性 LLM 直接决定交易所订单数值

### F. TradingAgents-CN

仓库：
https://github.com/hsliuping/TradingAgents-CN

采纳：

- 中文提示词和报告结构
- 国内可用模型 Provider 配置
- 模型配置持久化
- Docker 和中文前端使用体验

限制：

其 `app/` 和 `frontend/` 存在专有授权边界，不能直接复制。只参考开放部分和交互设计。

### G. Vibe-Trading

仓库：
https://github.com/HKUDS/Vibe-Trading

采纳：

- Hypothesis Registry
- Alpha Zoo 的元数据、Warmup、字段依赖和 Lookahead 禁止规则
- 同宇宙随机对照
- OOS 严格门控
- Run Card / Trust Layer
- 多 Agent DAG
- 数据源自动降级但显式标记来源
- 研究记忆和实验追踪

不采纳：

- 其大量横截面股票 Alpha 不直接套到 BTC/ETH 单标的 15m
- 它定位为研究和模拟，不提供实盘执行；不能用它证明订单链可靠

### H. QuantConnect Lean

仓库：
https://github.com/QuantConnect/Lean

采纳：

- Event-driven 算法生命周期
- Alpha、Portfolio Construction、Risk、Execution 分层
- 可插拔模型
- 研究、回测、优化和 Live 命令分离
- Consolidator/多周期数据的设计

不采纳：

- 不引入完整 .NET 引擎
- 不复制其庞大的多资产抽象

### I. vn.py

仓库：
https://github.com/vnpy/vnpy

采纳：

- Gateway、Engine、App 解耦
- CTA、Portfolio Strategy、Algo Trading 分离
- TWAP、Sniper、Iceberg、BestLimit 等执行算法概念
- Paper Account 与真实 Gateway 分离
- 组合监控和数据管理模式

### J. daily_stock_analysis

仓库：
https://github.com/ZhuLinsen/daily_stock_analysis

采纳：

- 多数据源降级和来源显示
- LLM Provider 配置和使用量统计
- 异步任务、SSE 进度
- 报告诊断
- 决策仪表盘结构
- 未接通时明确显示，不用 0 或默认值冒充

不采纳：

- 股票新闻和基本面策略不直接迁移到加密货币
- “AI 给买卖点”的展示不能代替可回测策略

## 3.3 第三优先级：仅借鉴概念

### K. Hummingbot

仓库：
https://github.com/hummingbot/hummingbot

适合借鉴：

- Exchange Connector
- Clock
- Order Tracker
- 高频挂撤单和 Market Making 的风险模型
- 多交易所归一化

不适合当前首期：

- 当前项目目标是 BTC/ETH 方向交易，不是做市和亚秒级 HFT
- 不应把 Avellaneda-Stoikov 等做市策略混入 15m 策略组合

### L. Superalgos

仓库：
https://github.com/Superalgos/Superalgos

适合借鉴：

- 可视化数据血缘和策略流程
- 图表、数据挖掘、回测、Paper、Live 的一体化体验
- 每个节点显示输入输出

不适合：

- 不迁移其巨大前端和节点系统
- 不复制其复杂生态和 Token 相关内容

### M. Qbot

仓库：
https://github.com/UFund-Me/Qbot

适合借鉴：

- 因子库、策略池、研究—回测—模拟—交易分层
- ML/RL 作为独立研究模块

限制：

- 项目较广且大量股票组件
- README 和仓库许可证信息存在需要人工核验的边界
- 不直接复制代码

### N. abu

仓库：
https://github.com/bbfamily/abu

适合借鉴：

- 买入因子、卖出因子、仓位因子、选股因子分离
- 交易过滤器
- 支撑阻力识别
- 失败交易判别

限制：

- GPL-3.0
- 技术栈和代码年代较旧
- 只借鉴因子拆分思想

---

# 4. 保留、替换和删除清单

## 4.1 保留

- 策略 Registry
- Candidate 版本化
- OOS Evidence 和 Manifest
- Config Snapshot
- Gatekeeper 的入场风险部分
- Strategy Library 的分目录思想
- 非晋升 Observation/Sampling Lane
- 回测报告和 Trade Ledger
- 决策漏斗
- Mainnet 默认关闭

## 4.2 替换

| 当前部分 | 替换为 |
|---|---|
| 4h/1h 强制 fail-closed | 概率化 Regime Context 和策略适配度 |
| 单一 SignalEnsemble | 多策略独立 Candidate + Ranker |
| 固定 2R | 策略特定多阶段退出 |
| 通用 reference price | 预交易实时快照 + 真实成交均价 |
| LLM veto | 有边界、可观察的 AI Market Committee |
| 单个 MetaLabel 硬门 | 校准后的概率评分，先 Shadow |
| 手工报告散落 | Immutable Evidence Package |
| 回测与实时两套计算 | 同一 Feature/Strategy 实现 |

## 4.3 最终删除

以下内容先 Deprecated + Shadow，V2 通过后删除：

- `operator_heuristic_v1` 的可执行权限
- `trend_momentum_v1` 的正式 Candidate 权限
- `trend_breakout_v1` 的正式 Candidate 权限
- 所有策略共用固定 2R 的路径
- 绝对 4h/1h 门控
- 无调用记录的 LLM veto
- 无时间戳、无数据来源的特征
- 能从旧 K 线直接生成“当前成交价”的逻辑
- 回测专用信号通过不同代码路径重新实现的逻辑

`operator_heuristic_v1` 可保留为 Baseline，但永远不可进入策略晋升。

---

# 5. V2 目标架构

## 5.1 核心数据合同

### MarketSnapshot

```python
class MarketSnapshot(BaseModel):
    symbol: str
    exchange: str
    decision_time: datetime

    bars_1m: BarWindow
    bars_5m: BarWindow
    bars_15m: BarWindow
    bars_1h: BarWindow
    bars_4h: BarWindow

    mark_price: Decimal
    index_price: Decimal
    best_bid: Decimal
    best_ask: Decimal
    spread_bps: float

    trades: TradeWindow
    order_book: OrderBookSnapshot | None

    funding_rate: float | None
    funding_time: datetime | None
    open_interest: Decimal | None
    open_interest_change: float | None
    liquidations: LiquidationWindow | None

    exchange_timestamp: datetime
    received_at: datetime
    freshness: FreshnessStatus
    source_ids: list[str]
```

### FeatureSnapshot

```python
class FeatureSnapshot(BaseModel):
    snapshot_id: str
    symbol: str
    decision_time: datetime
    feature_version: str

    structure: StructureFeatures
    trend: TrendFeatures
    momentum: MomentumFeatures
    volatility: VolatilityFeatures
    volume: VolumeFeatures
    derivatives: DerivativesFeatures
    microstructure: MicrostructureFeatures
    session: SessionFeatures

    input_hash: str
```

### RegimeAssessment

```python
class RegimeAssessment(BaseModel):
    trend_up: float
    trend_down: float
    range: float
    squeeze: float
    expansion: float
    panic: float
    illiquid: float
    dominant_regime: str
    confidence: float
    reasons: list[str]
```

概率总和不要求为 1，因为状态可能重叠；`dominant_regime` 由规则确定。

### StrategyCandidate

```python
class StrategyCandidate(BaseModel):
    candidate_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    direction: Literal["long", "short"]

    generated_at: datetime
    signal_bar_time: datetime
    expires_at: datetime

    setup_type: str
    entry_trigger: EntryTrigger
    invalidation: InvalidationRule
    stop_model: StopModel
    target_model: TargetModel
    max_holding_bars: int

    raw_score: float
    regime_fit: float
    setup_quality: float
    evidence_weight: float
    expected_cost_bps: float

    feature_snapshot_id: str
    regime_assessment_id: str
    evidence_manifest_id: str
    explanation_codes: list[str]
```

### AIAnalysis

```python
class AIAnalysis(BaseModel):
    invocation_id: str
    market_scenario: str
    candidate_rankings: list[CandidateRanking]
    conflicts: list[str]
    risk_flags: list[str]
    bounded_adjustments: dict[str, float]
    summary: str
```

`bounded_adjustments` 每个候选只能在 `[-0.10, +0.10]`，AI 不得凭空创建 Candidate。

### ExecutionPlan

```python
class ExecutionPlan(BaseModel):
    candidate_id: str
    source_snapshot_id: str

    order_type: Literal["market", "limit"]
    side: Literal["buy", "sell"]
    requested_quantity: Decimal

    pretrade_mark_price: Decimal
    maximum_price_drift_bps: float
    expires_at: datetime

    stop_distance_model: StopModel
    target_model: TargetModel

    reduce_only: bool = False
```

成交之后生成 `ProtectionPlan`，所有绝对价格从真实 `average_fill_price` 重算。

---

# 6. 统一特征库

新增目录：

```text
services/strategy_library/features/
    contracts.py
    price_structure.py
    trend.py
    momentum.py
    volatility.py
    volume.py
    derivatives.py
    microstructure.py
    session.py
    registry.py
```

## 6.1 市场结构

必须机械化，禁止模糊的“看起来像支撑”：

- Confirmed Swing High / Swing Low
- Rolling High / Low
- Donchian High / Low / Mid
- Structure Break
- Failed Structure Break
- Range Boundaries
- Distance to nearest support/resistance
- Wick ratio
- Close location value
- Gap/imbalance 只保留可机械定义的版本
- Reclaim / rejection

所有结构点必须：

- 只使用当时已经闭合的数据
- 指明确认延迟
- 记录首次可用时间
- 避免 ZigZag 后视重绘

## 6.2 趋势

- 多周期收益
- EMA 斜率和间距
- Donchian 趋势
- ADX/DMI
- 回归斜率和 R²
- 趋势持续时间
- 与 VWAP 的距离

EMA、ADX 不再直接构成策略，只作为特征。

## 6.3 动量

- RSI
- MACD Histogram 和变化率
- ROC
- 多周期收益
- 价格加速度
- 动量背离的机械定义

## 6.4 波动率

- ATR
- ATR Percentile
- Realized Volatility
- Bollinger Width
- Keltner Width
- Volatility Expansion/Contraction
- Gap/Jump Score

## 6.5 成交量

- Relative Volume
- Volume Z-score
- Taker Buy/Sell Imbalance
- Cumulative Volume Delta 近似
- Volume Trend
- Price-Volume Divergence
- 突破时成交量确认

## 6.6 衍生品

- Funding Rate 和 Z-score
- Funding 到下一结算的时间
- Open Interest 变化
- OI 与价格的联合状态
- Basis/Premium
- Liquidation Imbalance
- 大额清算后是否出现 Reclaim

## 6.7 微观结构

首期仅作为入场质量和执行过滤：

- Bid/Ask Spread
- Top-of-book imbalance
- L2 depth imbalance
- 短时 AggTrade imbalance
- 预期滑点
- Book freshness
- Order flow acceleration

不以单一订单簿失衡直接开仓。

## 6.8 时段

- UTC 小时
- 亚洲、欧洲、美洲重叠
- 周末
- Funding 周期
- 距离重大已知事件的时间

时段是特征，不是人为写死“周末必不交易”。

---

# 7. Regime Engine V2

## 7.1 第一阶段：确定性状态模型

创建：

```text
services/strategy_library/regime/regime_engine_v2.py
```

状态：

- `TREND_UP`
- `TREND_DOWN`
- `RANGE`
- `SQUEEZE`
- `EXPANSION`
- `PANIC`
- `ILLIQUID`
- `UNCERTAIN`

示例评分：

```text
trend_up =
  0.30 × normalized_ema_slope
+ 0.20 × directional_adx
+ 0.20 × positive_donchian_position
+ 0.15 × positive_return_consistency
+ 0.15 × volume_confirmation

range =
  0.30 × low_adx
+ 0.25 × low_regression_r2
+ 0.20 × repeated_boundary_rejections
+ 0.15 × mean_cross_frequency
+ 0.10 × stable_atr

squeeze =
  0.40 × low_bollinger_width_percentile
+ 0.30 × low_atr_percentile
+ 0.20 × volume_contraction
+ 0.10 × range_age
```

4h、1h、15m 不再是固定链条：

- 4h：慢速背景和大趋势风险
- 1h：当前状态和主要结构
- 15m：策略 Setup
- 1m/盘口：执行确认和价格漂移

高周期与候选方向冲突时降低 `regime_fit`，而非一律否决。只有极强冲突或风险状态才拒绝。

## 7.2 第二阶段：HMM Shadow

新增 HMM 仅做 Shadow：

- 输入：收益、波动率、成交量、趋势强度、Funding、OI
- 2～4 个状态
- 只能用训练窗口拟合
- 每个 Walk-forward 窗口重新拟合
- 标签通过状态统计解释，不人工先验命名
- 连续稳定三个 OOS 窗口后，才允许影响 Candidate Score
- HMM 永远不能直接产生订单

---

# 8. 策略组合

首期只上线四个互补策略，避免重新堆积几十个候选。

## 8.1 `trend_pullback_v2`

### 适用状态

- `TREND_UP` 或 `TREND_DOWN`
- 趋势概率 >= 0.60
- `ILLIQUID` 和 `PANIC` 不得激活
- 价格没有过度偏离趋势均值

### Long Setup

1. 1h/15m 趋势斜率为正。
2. 价格回调至 EMA20/EMA50、VWAP、Donchian Mid 或已确认结构支撑附近。
3. 回调成交量低于推动段成交量。
4. 出现收回、下影拒绝或局部结构重新转强。
5. MACD/RSI 只作为质量评分，不是硬阈值。
6. 下一根闭合 15m 或 5m 突破确认点。

Short 对称。

### 入场

- 默认确认突破 Market/Marketable Limit
- 信号有效期最多 1～2 根 15m
- 价格偏离过大则取消，不追价

### 止损

```text
max(
    最近确认 Swing 失效距离,
    1.1～1.5 ATR
)
```

从实际成交均价计算并校验。

### 退出

- TP1：1R 或最近结构目标，平 30%
- TP2：2R 或下一结构，平 30%
- 尾仓：40%，ATR Chandelier 或趋势斜率反转退出
- 最大持仓时间
- 趋势状态失效退出

## 8.2 `breakout_expansion_v2`

### 适用状态

- `SQUEEZE` 转 `EXPANSION`
- 明确水平区间
- 突破前波动率和成交量收缩

### Setup

1. Donchian/结构边界被有效突破。
2. 收盘在区间外。
3. Relative Volume 提升。
4. Spread 和预期滑点可接受。
5. OI/订单流仅做确认，不做单独信号。
6. 首次突破过度延伸则等待回踩，不直接追。

### 假突破保护

- 突破后 1～2 根 K 线重新回区间则快速退出。
- 无延续且成交量下降时 Time Stop。
- 不使用统一固定 2R。

### 退出

- TP1：区间高度的 0.5 倍或 1R
- TP2：区间完整测量目标
- 尾仓：ATR Trailing
- 回到区间内强制退出

## 8.3 `failed_breakout_reversal_v1`

这是用户截图案例最接近的策略。

### Short Setup

1. 最近有效阻力或摆动高点已存在。
2. 当前 K 线 High 突破阻力。
3. Close 重新收回阻力下方。
4. 上影线比例达到阈值。
5. 突破幅度不超过极端追价阈值。
6. 成交量放大但收盘没有延续，或订单流显示买方耗竭。
7. 后续 K 线跌破信号 K 线低点或短周期结构转弱。

Long 对称。

### 入场

- 确认 K 线突破触发
- 或 Reclaim 后回测失败触发
- 过期即取消

### 止损

```text
sweep_extreme + max(tick_buffer, 0.15～0.30 ATR)
```

### 退出

- TP1：区间中轴或 0.8～1R，平 35%
- TP2：区间另一侧前的结构位，平 35%
- 尾仓：30%，直到结构反转或下一个流动性池
- 信号后没有跟随，1～3 根 15m 内 Time Stop

## 8.4 `range_sweep_reversion_v1`

### 适用状态

- `RANGE` 概率高
- 边界至少被多次确认
- 趋势强度低
- 区间宽度足以覆盖成本和目标

### Setup

1. 价格扫过区间边界。
2. 收盘重新回到区间。
3. 成交量/订单流出现耗竭。
4. 不处于强趋势扩张状态。
5. 入场方向指向区间均值。

### 退出

- TP1：区间中轴，平 50%
- TP2：对侧边界前，平 30%
- 尾仓：20%，仅在形成新趋势时保留
- 一旦区间被有效突破则止损

## 8.5 Phase 2：`volatility_squeeze_v1`

只有前四个策略稳定后才实现：

- Bollinger/Keltner/ATR 收缩
- 成交量收缩
- 区间成熟
- 突破后成交量和盘口确认

## 8.6 Experimental：`liquidation_exhaustion_v1`

只允许 Shadow：

- 清算量异常
- OI 快速下降
- 价格扫损后 Reclaim
- Spread 恢复
- 订单流从单边转平衡

至少 300 个历史事件和独立 OOS 通过后才考虑执行。

---

# 9. Candidate Ranker 和冲突消解

创建：

```text
services/strategy_library/ensemble/candidate_ranker_v2.py
```

基础评分：

```text
FinalScore =
    0.22 × EvidenceWeight
  + 0.22 × RegimeFit
  + 0.20 × SetupQuality
  + 0.12 × ExecutionQuality
  + 0.10 × LiquidityQuality
  + 0.08 × CrossFeatureConfirmation
  + 0.06 × AIAdjustment
  - RiskPenalty
  - CorrelationPenalty
  - StalenessPenalty
```

其中 AIAdjustment 最终贡献不超过总分的 6%，且 AI 原始调整限制在 ±10%。

## 9.1 冲突规则

- 同标的多空同时出现：默认不交易，除非一方分数领先至少配置阈值且另一方证据弱。
- 同方向多个策略：合并为一个 Execution Intent，保留各策略归因，不重复开仓。
- BTC 和 ETH 同方向高相关：组合层削减总风险，不视为两个完全独立机会。
- 已有仓位：新 Candidate 只能选择忽略、加仓建议或退出建议；首期禁止自动加仓。
- 过期 Candidate：不得重新使用。

## 9.2 频率目标

不是硬保证，作为设计监控：

- BTC+ETH 合计每月产生 20～80 个可解释 Candidate
- 经风险和执行过滤后每月约 8～30 笔 Testnet 自然交易
- 若低于范围，先看各策略覆盖和漏斗，不降低 Exchange-First、数据新鲜度和风险安全门
- 若高于范围，检查策略重叠、过度交易和费用侵蚀

---

# 10. AI Market Committee

## 10.1 离线 Research Agents

创建：

```text
services/agents/research/
    source_collector.py
    license_reviewer.py
    strategy_translator.py
    hypothesis_manager.py
    backtest_designer.py
    skeptic_reviewer.py
    evidence_reviewer.py
```

职责：

### Source Collector

- 搜索论文、开源仓库和官方文档
- 保存 URL、Commit SHA、发布日期、许可证
- 标记股票/期货/加密、周期、数据类型
- 不直接写入生产 Strategy

### Strategy Translator

把外部策略转成标准 `StrategySpec`：

```yaml
strategy_id:
source:
license:
market:
timeframe:
required_features:
warmup:
entry_rules:
invalidation:
stop_model:
target_model:
cost_assumptions:
known_failure_regimes:
```

### Hypothesis Manager

每个策略实验必须有：

- 假设
- 预期适用状态
- 预期失败状态
- 固定指标
- 数据窗口
- 结果
- 是否淘汰
- 后续禁止重复尝试的原因

### Skeptic Reviewer

主动寻找：

- Lookahead
- Survivorship bias
- 数据泄漏
- 参数过拟合
- 成本遗漏
- 不合理成交
- 统计样本不足

AI 生成的代码不能自动合并；必须经过测试和证据门。

## 10.2 实时 Agents

只保留四个角色：

1. `TechnicalStructureAgent`
2. `DerivativesMicrostructureAgent`
3. `RegimeAgent`
4. `RiskSkepticAgent`

由 `MarketCommittee` 汇总。

每个 Agent 输入的是同一个 `MarketSnapshot + FeatureSnapshot + Candidates`，不允许各自从网络抓不同的价格。

### 输出

```json
{
  "market_scenario": "failed_breakout_reversal",
  "candidate_assessments": [
    {
      "candidate_id": "...",
      "support": 0.72,
      "conflicts": [],
      "risk_flags": ["weekend_low_liquidity"],
      "adjustment": 0.06
    }
  ],
  "summary": "..."
}
```

### 边界

AI 可以：

- 场景分类
- 候选排序
- 解释冲突
- 识别新闻和异常风险
- 建议跳过低质量 Candidate

AI 不可以：

- 没有规则 Candidate 时凭空下单
- 直接生成任意绝对价格
- 修改实际成交事实
- 把无成交订单标记为成交
- 拦截硬止损或 ReduceOnly 紧急退出
- 超过仓位/风险限制
- 自动修改生产参数

## 10.3 调用保证和可观察性

新增 `llm_invocations`：

- Provider
- Model
- Agent Role
- Prompt/Response Hash
- Input Snapshot ID
- Candidate ID
- Status
- Skip Reason
- Latency
- Token Usage
- Estimated Cost
- Retry Count
- Checkpoint ID

运行规则：

- 每小时至少一次 Market Review，确保 AI 接口真实工作。
- 每个排名进入前 N 的 Candidate 触发一次 Committee。
- Provider 故障时 Testnet 可按确定性 Ranker 继续，但必须展示降级状态。
- 生产阶段是否允许降级由独立配置决定。
- Forced Exit 永远不调用 AI。

---

# 11. 自适应退出与仓位管理

创建：

```text
services/strategy_library/exit/adaptive_exit_v2.py
services/strategy_library/exit/target_ladder.py
services/strategy_library/exit/trailing_models.py
services/strategy_library/exit/time_stop.py
```

## 11.1 统一规则

- 所有止损和止盈绝对价格从真实平均成交价计算。
- 止损必须位于交易方向的亏损侧。
- 止盈必须位于盈利侧。
- 所有数量 ReduceOnly。
- 每次部分成交后按剩余真实仓位重算。
- 本地 Protection 只有取得 Binance Order ID 后才能 ACTIVE。
- 不允许因最小名义价值扩大平仓数量。

## 11.2 不采用统一固定 2R

每个策略定义自己的 ExitProfile：

```python
class ExitProfile(BaseModel):
    initial_stop: StopModel
    targets: list[TargetLeg]
    trailing: TrailingModel | None
    time_stop: TimeStop | None
    invalidation_exit: InvalidationRule
```

## 11.3 Break-even 规则

不能机械地第一止盈后立即移到开仓价。每个策略单独验证：

- 假突破策略：TP1 后可移至信号结构失效点
- 趋势策略：过早保本可能切断趋势，应使用 ATR/结构 Trailing
- 区间策略：接近均值后可收紧止损
- 所有规则纳入消融测试

---

# 12. 回测和验证 V2

新增：

```text
services/validation/event_replay.py
services/validation/execution_models.py
services/validation/portfolio_simulator.py
services/validation/walk_forward.py
services/validation/block_bootstrap.py
services/validation/monte_carlo.py
services/validation/random_control.py
services/validation/lookahead_audit.py
services/validation/recursive_audit.py
services/validation/evidence_package.py
```

现有 `technical_replay.py` 进入兼容模式，逐步拆分后删除。

## 12.1 成交模型

每个策略至少输出三种结果：

1. `NEXT_BAR_OPEN`
2. `NEXT_BAR_VWAP`
3. `QUOTE_BASED`：Bid/Ask + 深度/滑点

严禁同根 K 线收盘确认后仍按该收盘价无延迟成交。

## 12.2 成本

- Maker/Taker Fee
- 动态 Spread
- 滑点
- Funding
- 部分成交
- 拒单
- 延迟
- 盘口深度不足
- Testnet 与 Mainnet 成本模型分离

## 12.3 Walk-forward

真正流程：

```text
训练窗口
→ 参数/模型选择
→ 锁定
→ 完全未见 OOS
→ 向前滚动
→ 再训练
```

不能只把一次完整回放切成多个报告窗口并称为 Walk-forward。

## 12.4 Bootstrap 和 Monte Carlo

- Moving Block Bootstrap
- Stationary Bootstrap
- Trade sequence shuffle
- Candle perturbation
- Slippage stress
- Funding stress
- Entry delay stress
- Random entry control
- Same-universe random factor control

## 12.5 参数稳定性

正式 Candidate 必须满足：

- 最佳参数周围至少 60% 邻域仍为正
- 不能只在一个尖峰参数盈利
- 不同 BTC/ETH、不同年份和不同 Regime 方向一致
- 参数变化不能导致交易数量或收益突然断崖式变化

## 12.6 分阶段晋升门

### Gate A：代码正确

- 单元测试
- Lookahead Audit
- Recursive Audit
- 同一 Snapshot 结果确定
- 实时/回放特征一致

### Gate B：研究候选

- OOS >= 100 笔
- 净期望点估计 > 0
- PF >= 1.30
- 无明显参数尖峰
- 成本压力后未崩溃

### Gate C：正式候选

- OOS >= 200 笔
- WinRate >= 50%
- AvgWin/AvgLoss >= 1.20
- PF >= 1.50
- MaxDrawdown <= 15%
- 90% CI 下界接近或高于 0
- 多 Regime 盈利来源不过度集中

### Gate D：Shadow

- 连续运行至少 30 天
- 实时和回放 Candidate 一致率 >= 99%
- 无旧数据、重绘或过期执行
- 所有无交易均可解释

### Gate E：Binance Testnet

- 至少 50 笔自然 Scheduler 完整交易
- Exchange First 100%
- 零幽灵仓位
- 零未处理裸仓
- 入场价格偏移符合护栏
- PnL 与 Binance 成交/手续费可对账

Mainnet 不属于本方案放行范围。

---

# 13. 文件级实施方案

## 13.1 第一批：冻结合同

创建：

```text
services/strategy_library/contracts.py
services/data/market_snapshot.py
services/strategy_library/features/contracts.py
services/strategy_library/regime/contracts.py
services/agents/contracts.py
services/validation/contracts.py
```

修改：

```text
services/strategy_library/models.py
services/strategy_library/registry.py
services/strategy_library/runner.py
```

要求：

- 原有对象通过 Adapter 转成 V2 合同。
- 不在旧模型上继续添加可选字段。
- 所有合同有 Schema Version。
- 序列化测试和 Hash 测试。

## 13.2 第二批：统一 Feature Engine

创建前述 `features/` 文件。

修改：

```text
services/strategy_library/technical/*
services/execution/decision_pipeline.py
services/validation/technical_replay.py
```

要求：

- 回测和实时只调用同一个 Feature Engine。
- 指标不得在候选内部重复计算。
- 每个特征有 Warmup 和 Freshness。
- 同一输入 Hash 的 FeatureSnapshot 必须完全一致。

## 13.3 第三批：Regime V2

创建：

```text
services/strategy_library/regime/regime_engine_v2.py
tests/services/strategy_library/regime/test_regime_engine_v2.py
```

旧 4h/1h Gate 保留为 Shadow 对照，不再拥有正式否决权。

## 13.4 第四批：四个策略

创建：

```text
services/strategy_library/candidates/trend_pullback_v2.py
services/strategy_library/candidates/breakout_expansion_v2.py
services/strategy_library/candidates/failed_breakout_reversal_v1.py
services/strategy_library/candidates/range_sweep_reversion_v1.py
```

每个文件只负责：

```text
FeatureSnapshot + RegimeAssessment
→ 0 或 1 个 StrategyCandidate
```

不下单、不查数据库、不调用 LLM、不自行计算仓位。

## 13.5 第五批：Ranker 和 Exit

创建：

```text
services/strategy_library/ensemble/candidate_ranker_v2.py
services/strategy_library/exit/adaptive_exit_v2.py
services/strategy_library/exit/target_ladder.py
```

修改：

```text
services/execution/decision_pipeline.py
services/execution/paper_signal.py
```

## 13.6 第六批：AI Committee

创建：

```text
services/agents/market_committee.py
services/agents/technical_structure_agent.py
services/agents/derivatives_agent.py
services/agents/regime_agent.py
services/agents/risk_skeptic_agent.py
services/agents/invocation_repository.py
```

修改：

```text
services/agents/llm_factory.py
services/agents/llm_runtime.py
services/agents/service.py
services/execution/decision_pipeline.py
```

## 13.7 第七批：Validation V2

实现事件回放和所有验证模块，现有候选与 V2 候选并行跑。

## 13.8 第八批：执行接入

仅在此前 Exchange-First 修复已通过后：

修改：

```text
services/execution/paper_cycle_orchestrator.py
services/execution/paper_exchange_execution.py
services/execution/paper_order_lifecycle.py
```

流程必须是：

```text
Candidate
→ AIAnalysis
→ ExecutionPlan
→ Pretrade Snapshot
→ Binance Order
→ ExchangeFillReceipt
→ Local Position Projection
→ ProtectionPlan from Average Fill
```

## 13.9 第九批：旧策略退役

达到 Gate C～E 后：

- 将 V1 权重设为 0
- 保留 7～14 天 Shadow 对照
- 删除执行注册
- 迁移历史报告
- 最后删除旧代码

---

# 14. 防止再次返工的工程规则

## 14.1 不原地改 V1

新实现全部使用新版本：

- `trend_pullback_v2`
- `breakout_expansion_v2`
- `regime_engine_v2`
- `candidate_ranker_v2`
- `adaptive_exit_v2`

V1 只修安全 Bug，不再增加功能。

## 14.2 接口先冻结

在写策略前先确认六个合同：

1. MarketSnapshot
2. FeatureSnapshot
3. RegimeAssessment
4. StrategyCandidate
5. AIAnalysis
6. ExecutionPlan

合同通过评审后，后续只能新增向后兼容版本，不能随意改字段语义。

## 14.3 一个 PR 只改一层

禁止同一 PR 同时：

- 改数据
- 调策略参数
- 改下单
- 改前端

推荐提交边界：

```text
PR1 contracts
PR2 feature parity
PR3 regime
PR4 trend strategy
PR5 reversal strategies
PR6 exits
PR7 AI committee
PR8 validation
PR9 execution integration
PR10 frontend
```

## 14.4 每次参数变化都生成新证据

每个结果绑定：

- Git SHA
- Data Hash
- Feature Version
- Strategy Version
- Parameter Hash
- Cost Model
- Execution Model
- Random Seed
- Report ID

## 14.5 禁止优化器直接改生产配置

优化器只能输出 Candidate Parameter Proposal。必须经过：

- 稳定性测试
- 独立 OOS
- 人工/Agent Review
- Manifest 晋升

## 14.6 先事实正确，再优化收益

下列任何一项未通过时，禁止优化策略：

- 回放和实时特征不一致
- 价格时间错位
- 交易所无成交但本地有仓位
- 保护单无法确认
- PnL 不可对账
- 决策漏斗缺失
- AI 调用状态不可见

---

# 15. 必须编写的测试

## 15.1 特征

- 同一输入确定性
- Warmup
- K 线未闭合不得进入策略
- Swing 不重绘
- 无未来数据
- Funding/OI 时间点一致
- 数据源缺失显式降级

## 15.2 Regime

- 强趋势样本
- 区间样本
- Squeeze 样本
- Expansion 样本
- Panic 样本
- 不确定状态
- 高周期冲突降低评分而非默认全拒绝

## 15.3 策略

每个策略至少：

- 正向 Long
- 正向 Short
- 边界失败
- 过期
- 数据不足
- 不适用 Regime
- 成交量/结构冲突
- Stop/Target 模型正确

## 15.4 Ranker

- 同向合并
- 多空冲突
- 过期拒绝
- AI 上限
- BTC/ETH 相关风险
- 无 Candidate 时绝不让 AI 创建订单

## 15.5 AI

- Provider 真调用 Smoke
- Token 记录
- 缺 Key
- 超时
- 无效 JSON
- 重试
- Checkpoint
- 确定性降级
- Forced Exit 不调用 AI

## 15.6 回测

- 同根成交禁止
- 下一根开盘
- VWAP
- Quote
- Funding
- 费用
- 滑点
- 部分成交
- Lookahead
- Recursive
- Block Bootstrap
- Walk-forward

## 15.7 实时

- 候选过期
- 价格漂移
- 交易所拒单
- 提交后状态未知
- 部分成交
- Protection 失败
- ReduceOnly
- 重启恢复
- 对账失败
- 本地/交易所一致

---

# 16. 分阶段实施与退出条件

## Phase 0：基线冻结，2～3 个工作日

输出：

- 当前 V1 不可变回测报告
- 当前自然交易漏斗统计
- 当前所有幽灵单证据
- 数据字典
- 六个合同设计
- License Review

退出条件：

- 所有人对合同字段和语义无异议
- 不再改 V1 逻辑

## Phase 1：数据与特征一致性，5～8 个工作日

输出：

- MarketSnapshot
- Feature Engine
- Backtest/Live parity test
- 数据新鲜度和来源

退出条件：

- 同一输入 Hash 的特征 100% 一致
- 无未闭合 K 线
- 无时区错位
- 所有特征有时间戳

## Phase 2：Regime 和策略，8～12 个工作日

输出：

- Regime V2
- 四个策略
- Candidate Ranker
- 单元测试
- Shadow 报告

退出条件：

- 四个策略都能在构造场景中产生和拒绝信号
- 每个 Candidate 可解释
- 无策略直接调用下单

## Phase 3：Exit V2，4～6 个工作日

输出：

- Strategy-specific exits
- 分批退出
- Time Stop
- Trailing
- 失效退出

退出条件：

- 所有绝对价格可从实际成交价重建
- ReduceOnly 数量正确
- 不再依赖固定 2R

## Phase 4：AI Committee，5～8 个工作日

输出：

- 四 Agent
- Committee
- Provider Registry
- Invocation Ledger
- Smoke Test
- 前端 API

退出条件：

- API 有真实 Token 用量
- 每次调用/跳过可追踪
- AI 无法创建 Candidate 或阻止硬退出

## Phase 5：Validation V2，8～15 个工作日

输出：

- Event Replay
- 三种成交模型
- Cost/Funding
- Walk-forward
- Bootstrap/Monte Carlo
- Random Control
- Evidence Package

退出条件：

- V2 和 V1 并行报告
- 没有 Lookahead/Recursive 问题
- 参数稳定性可见

## Phase 6：Shadow，至少 30 天

输出：

- 每根决策 K 线的 V1/V2 对比
- 实时/回放一致率
- Candidate 频率
- AI 记录
- 未交易原因

退出条件：

- 一致率 >= 99%
- 无过期候选执行
- 无数据源静默退化
- 达到 Gate C 或明确淘汰

## Phase 7：Binance Testnet，至少 50 笔自然完整交易

输出：

- Entry/Fill/Protection/Exit Evidence
- PnL 对账
- 滑点和拒单统计
- 策略归因
- Regime 归因

退出条件：

- 零幽灵单
- 零错误持仓接管
- 零无保护裸仓未升级
- 所有交易有 Candidate、AI、Fill、Protection 和 Exit 链接

## Phase 8：退役 V1

只有 Phase 7 通过后执行。

---

# 17. 最终前端需要展示

## 实时策略页

- 当前 Regime 概率
- 主要结构位
- 每个策略是否产生 Candidate
- Candidate 分数拆解
- AI Committee 观点
- 为什么开/不开
- 数据时间和来源

## 交易详情页

- Signal Bar
- Signal Reference Price
- Pretrade Mark/Bid/Ask
- Actual Fill
- SL/TP 计算
- 每次部分退出
- Binance Order/Trade ID
- 本地投影
- PnL/费用/Funding

## 策略证据页

- OOS 指标
- Walk-forward
- 参数稳定性
- Regime 归因
- Monte Carlo
- 当前晋升 Gate
- 当前策略版本和 Git SHA

## AI 使用页

- Provider/Model
- Token
- Cost
- Latency
- Error
- Skip Reason
- 输入 Snapshot
- 输出和最终决策差异

---

# 18. 项目最终定义

完成本次重构后，这个系统不再是：

```text
几条指标规则
+ 一个很少调用的 LLM veto
+ 一套容易与交易所错位的本地 Paper 状态
```

而是：

```text
事件驱动、点时一致的数据底座
+ 市场状态适配的多策略组合
+ 有边界、可观察的 AI 市场委员会
+ 策略特定的风险与退出
+ Exchange-First 的真实 Testnet 执行
+ 严格样本外、随机对照和压力验证
```

收益目标可以设置为：

```text
WinRate >= 50%
AvgWin/AvgLoss >= 1.20
ProfitFactor >= 1.50
NetExpectancy > 0
MaxDrawdown <= 15%
```

这些是晋升标准，不是未来收益保证。任何策略达不到，就淘汰，不为了“系统必须开单”而把它包装成有效策略。

---

# 19. 实施顺序的一句话版本

```text
先完成执行真实性
→ 冻结数据与策略合同
→ 建统一特征库
→ 上线四个互补策略
→ 改成策略特定退出
→ 接入有边界 AI
→ 做事件级严格验证
→ Shadow 30 天
→ Testnet 50 笔自然交易
→ 再删除 V1
```

不要再次从“改 MACD/RSI 参数”开始，也不要从“让 AI 自由看图下单”开始。
