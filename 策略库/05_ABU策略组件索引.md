# ABU 策略组件索引

## 来源与边界

- 来源仓库：`https://github.com/bbfamily/abu`
- 许可证：GPL-3.0，已在 GitHub `LICENSE` 文件确认。
- 使用方式：只做研究蒸馏和 RAG 索引，不复制 ABU 运行时代码，不直接导入 ABU 类。
- 入库方式：任何想法必须转成 `StrategyIdea -> StrategyDraft -> StrategyContract -> Backtest -> Paper -> Review`。

## 目录级策略地图

### FactorBuyBu：买入/开仓因子

可观察组件：

- `ABuFactorBuyBreak.py`：突破类买入因子，可映射为 Donchian / 区间突破候选。
- `ABuFactorBuyTrend.py`：趋势类买入因子，可映射为 EMA/ADX/Dow 高低点趋势确认。
- `ABuFactorBuyDM.py`：方向运动类因子，可映射为 ADX/+DI/-DI 趋势过滤。
- `ABuFactorBuyWD.py`：周期/星期类择时思想，币圈可改造为交易时段、资金费率结算窗口、宏观事件窗口过滤。
- `ABuFactorBuyBase.py`：买入因子基类思想，可映射为本项目统一 `entry_rules` schema。

优先迁移候选：

1. 趋势突破：4h EMA/ADX/Dow 同向，15m 突破或回踩确认。
2. 假跌破反转：15m 刺破支撑后收回，仅在 4h 不空头时做多。
3. 资金费率窗口过滤：资金费率结算前后降低方向性开仓频率。

### FactorSellBu：卖出/平仓/止损因子

可观察组件：

- `ABuFactorAtrNStop.py`：ATR N 倍止损思想。
- `ABuFactorCloseAtrNStop.py`：收盘价触发的 ATR 止损思想。
- `ABuFactorPreAtrNStop.py`：前置/跟踪类 ATR 止损思想。
- `ABuFactorSellBreak.py`：跌破/突破失败后的退出思想。
- `ABuFactorSellNDay.py`：时间止损/持仓周期上限思想。
- `ABuFactorSellBase.py`：卖出因子基类思想，可映射为本项目统一 `exit_rules` / `stoploss_rules` / `takeprofit_rules`。

已进入本项目的部分：

- ATR 止损：`stoploss_rules.atr_multiple`。
- 固定 bps 止损：`stoploss_rules.fixed_bps`。
- R 倍止盈：`takeprofit_rules.risk_reward`。
- 达到 `trail_after_r` 后移动止损到入场价。
- 反向信号平仓：`exit_rules.close_on_opposite_signal`。

下一步候选：

1. `max_hold_bars` 时间止损落到 runtime，而不是只存在于策略规则中。
2. 趋势失效退出：EMA 快慢线反穿、ADX 低于阈值、Dow 结构破坏。
3. 分批止盈：1R 减仓、2R 保留尾仓，必须先扩展订单/仓位模型支持部分平仓。

### PickStockBu：标的筛选

可观察组件：

- `ABuPickRegressAngMinMax.py`：线性斜率/角度筛选。
- `ABuPickSimilarNTop.py`：相似性筛选。
- `ABuPickStockPriceMinMax.py`：价格区间筛选。

币圈改造方向：

- Top20 不是最终选币策略，只是初始候选池。
- 需要引入流动性、点差、成交额、波动率、资金费率、OI、盘口深度过滤。
- 相似性筛选可用于“只交易当前最像历史有效样本的市场结构”，但必须经过样本外验证。

### UmpBu：交易裁判/二级过滤

可观察组件：

- `ABuUmpMain*`：主裁判类。
- `ABuUmpEdge*`：边际优势判断类。
- `ABuUmpManager.py`：裁判管理思想。

本项目映射：

- 对应 `SignalEnsemble + MetaLabel + Decision Veto Agent`。
- 当前 MetaLabel 仍是轻量统计过滤，不是完整训练模型。
- LLM 只做 veto/分类/复盘，不输出方向、价格、仓位。

优先优化：

1. 把 `meta_label_min_win_rate` 从固定阈值升级为按策略/市场状态记录的 OOS 统计。
2. 为每种信号保存胜率、盈亏比、平均持仓、失败原因。
3. 低样本数时降权，而不是过早信任短期胜率。

### SlippageBu：滑点模型

可观察组件：

- `ABuSlippageBuyMean.py` / `ABuSlippageSellMean.py`：均值滑点思想。
- `ABuSlippageBuyBase.py` / `ABuSlippageSellBase.py`：买卖两侧滑点模型边界。

本项目下一步：

- 回测和 Paper 统一加入 taker/maker 费率、盘口深度、冲击成本。
- 大波动、低流动性、新闻窗口下扩大滑点假设。
- 低于最小名义金额、盘口深度不足、价差过宽时拒绝开仓。

### MetricsBu：评价与优化

可观察组件：

- `ABuCrossVal.py`：交叉验证思想。
- `ABuGridSearch.py` / `ABuGridHelper.py`：参数网格搜索思想。
- `ABuMetricsBase.py` / `ABuMetricsFutures.py` / `ABuMetricsScore.py`：指标评价思想。

本项目映射：

- 已有 Validation Layer 门槛：Sharpe、Profit Factor、Max Drawdown、Expectancy。
- 已有 walk-forward / OOS / stress 文档与部分服务。
- 仍需要把新增 technical lane 的参数纳入统一优化和样本外评估。

## 对当前项目最适合先加的内容

P0：已做或本轮强化

- 技术通道不再无信号兜底开仓。
- 4h 方向 + 15m 入场成为默认 technical lane。
- RSI、EMA、ADX、VWAP、Bollinger、假突破/假跌破加入候选信号。
- Testnet 自动执行失败时本地不再继续填充成交。

P1：下一批实现

- `max_hold_bars` 时间止损。
- 趋势失效退出。
- 滑点/盘口深度过滤。
- 信号级 Review 统计：每个 signal source 的胜率、Profit Factor、失败原因。
- OI、Long/Short Ratio、Liquidation、Order Book 进入 Data Layer 后再加入技术通道。

P2：需要先规则化/验证

- 缠论买卖点：先定义客观分型、笔、线段、中枢、背驰算法，再进入回测。
- 相似性市场结构匹配：需要足够历史样本与严格样本外验证。
- 机器学习裁判：必须只输出是否下注/仓位系数，不直接给方向。

## 禁用清单

- 禁止复制 ABU GPL 源码到本项目 runtime。
- 禁止无止损开仓。
- 禁止 Martingale 和亏损加仓摊平。
- 禁止因为 LLM 或主观判断跳过 Validation/Gatekeeper。
- 禁止未回测的新参数直接进入 Testnet 自动执行。
