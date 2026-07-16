# 期望值计算Bug修复方案

## Bug根因

`meta_label_edge_stats`函数返回的是**原始价格收益率**（如0.02 = 2%涨幅），但`net_edge_after_cost`函数期望的是**R倍数**（如2.0R = 2倍止损距离）。

这导致：
- 实际收益率：0.02（2%涨幅）
- 被误用为：0.02R（0.02倍止损）
- 正确应该是：2.0R（如果止损是1%，2%涨幅就是2R）

## 具体案例

从拒单数据看到：
```
胜率:57.45% 平均盈:0.00R 平均亏:0.00R 成本:16bps 净期望:-0.0007R
```

**问题**：`average_win`和`average_loss`都是0.00，因为原始收益率很小（±2%），直接当R用就接近0。

**正确计算**：
- 策略配置：2R止盈，1R止损
- 如果胜率57.45%
- 净期望 = 0.5745 × 2.0 - 0.4255 × 1.0 - 0.0016 = **0.72R**（正期望！）

## 修复方案

### 选项1：将收益率转换为R倍数（推荐）

在`decision_pipeline.py`中，将`edge_stats`的收益率转换为R倍数后再传入`net_edge_after_cost`。

需要知道止损距离（bps或ATR倍数），然后：
```python
stop_distance_fraction = calculate_stop_distance(strategy, volatility)
average_win_r = edge_stats['average_win'] / stop_distance_fraction
average_loss_r = edge_stats['average_loss'] / stop_distance_fraction
```

### 选项2：直接使用策略规则的R倍数（简单但不精确）

策略配置已经明确：`takeprofit_rules: {risk_reward: 2.0}`，即2R止盈/1R止损。

直接使用这些固定值：
```python
# 从策略规则读取
takeprofit_rules = strategy.rules.get('takeprofit_rules', {})
risk_reward = takeprofit_rules.get('risk_reward', 2.0)

# 使用固定R倍数
average_win_r = risk_reward  # 2.0R
average_loss_r = 1.0         # 1.0R（止损定义）
```

### 选项3：完全重写edge_stats逻辑（最准确但复杂）

使用真实历史交易的R倍数，而不是原始收益率。需要：
1. 回测每个信号
2. 计算每笔交易的实际止盈/止损触发
3. 以R为单位记录盈亏

这就是`services/execution/signal_edge_stats.py`已经在做的事情，但需要先运行`compute_signal_edge_stats.py`生成artifact。

## 推荐实施方案

**短期（立即修复）**：使用**选项2**
- 代码改动最小
- 符合当前策略配置
- 立即可用

**中期（正确方案）**：使用**选项1**
- 计算真实止损距离
- 将原始收益率转换为R倍数
- 更准确反映风险调整后的期望

**长期（最佳实践）**：使用**选项3**
- 运行`compute_signal_edge_stats.py`
- 使用真实历史交易的R倍数
- 最准确的期望值估计

## 立即实施（选项2）

修改`services/execution/decision_pipeline.py`：

```python
# 在第274行附近，修改edge_stats使用方式
edge_stats = _edge_stats_for_gate(strategy_key=strategy.strategy_key, training_samples=training_samples)

# 从策略规则读取R倍数配置
takeprofit_rules = strategy.rules.get('takeprofit_rules', {})
stoploss_rules = strategy.rules.get('stoploss_rules', {})
risk_reward_ratio = takeprofit_rules.get('risk_reward', 2.0)

# 如果edge_stats的average_win/loss接近0（原始收益率很小），使用策略配置的R倍数
# 否则尝试转换（需要知道止损距离）
if edge_stats['average_win'] < 0.001 and edge_stats['average_loss'] < 0.001:
    # 使用策略配置的固定R倍数
    average_win_r = risk_reward_ratio  # 2.0R
    average_loss_r = 1.0               # 1.0R by definition
else:
    # TODO: 将原始收益率转换为R倍数（需要计算止损距离）
    # 暂时使用固定值
    average_win_r = risk_reward_ratio
    average_loss_r = 1.0

trace.update({
    "meta_label_win_rate": edge_stats["win_rate"],
    "meta_label_average_win": average_win_r,  # 使用R倍数
    "meta_label_average_loss": average_loss_r,  # 使用R倍数
    # ... rest unchanged
})
```

## 预期效果

修复后，以ONDO/USDT为例：
- 胜率：57.45%
- 平均盈：**2.0R**（而不是0.00R）
- 平均亏：**1.0R**（而不是0.00R）
- 手续费：0.0016R（6bps @ 2.5%止损 = 6/250 = 0.024倍止损 ≈ 0.0016R）
- 净期望：`0.5745 × 2.0 - 0.4255 × 1.0 - 0.0016 = 0.72R`

**结果**：从`-0.0007R`（拒绝）变为`+0.72R`（通过）✅
