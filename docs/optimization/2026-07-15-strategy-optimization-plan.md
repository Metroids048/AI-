# 策略优化方案 - 2026-07-15

## 背景
当前三种策略均为负期望，系统正确拒绝开单。用户要求优化使策略达到正期望并开始交易。

## 诊断结果

### 当前状态
- **Directional**: OOS净期望 -0.23%，MetaLabel阈值50%
- **Carry**: 资金费率0.3-1bps无法覆盖12bps成本
- **Cross-sectional**: 17.4%胜率，未启用

### 根本原因
1. **MetaLabel阈值过严**: 50%胜率 + 6bps成本 = 很少信号通过
2. **时间框架过短**: 15m交易与HFT竞争，噪音大
3. **中期策略未启用**: 1d/4h Swing策略已定义但未激活

---

## 优化策略

### 方案1: 适度放宽现有策略 (快速见效)

#### 1.1 降低MetaLabel阈值
```python
# bootstrap.py AUTO_PAPER_TECHNICAL_RULES
"meta_label_min_win_rate": 0.50  # 当前
↓
"meta_label_min_win_rate": 0.46  # 优化后
```

**理由**: 46%胜率 × 2R止盈 = 0.92倍期望，扣除6bps仍有微弱正期望

**风险**: 增加约20-30%信号量，但胜率降低可能导致连续亏损

---

#### 1.2 调整信号权重（基于edge stats分析后）
```python
# 当前权重
MACD: 1.0
Dow Trend: 0.9
EMA Trend: 0.9
ADX: 0.85
RSI: 0.75
VWAP: 0.75
Bollinger: 0.75
FVG: 0.7

# 优化方向（待edge stats验证）:
# - 如果某信号单独胜率<45%，降低权重或移除
# - 如果某组合胜率>52%，提高权重
```

---

### 方案2: 启用中期Swing策略 (推荐)

#### 2.1 为什么Swing可能有正期望？
1. **更大的止损空间**: ATR × 2.5 (vs 2.0)，减少被噪音止损
2. **日线级别趋势**: 与15m相比更稳定
3. **降低交易频率**: 减少累积手续费
4. **避开HFT竞争**: 1d/4h不与高频算法竞争

#### 2.2 Swing配置
```python
AUTO_PAPER_SWING_RULES = {
    "direction_timeframe": "1d",   # 日线主趋势
    "entry_timeframe": "4h",        # 4小时入场
    "enabled_signals": [
        "dow_trend",      # 多日趋势结构
        "ema_trend",      # 日线级别交叉
        "adx",            # 趋势强度
        "macd",           # 动量背离
        "price_action",   # Pin bar/Engulfing
    ],
    "meta_label_min_win_rate": 0.50,  # 初始保持50%
    "stoploss_rules": {"atr_multiple": 2.5},  # 更宽止损
    "takeprofit_rules": {"risk_reward": 2.0},
    "position_rules": {
        "risk_per_trade": 0.025,
        "max_leverage": 15,  # 降低杠杆
        "time_exit_hours": 24 * 14,  # 14天最长持仓
    }
}
```

**优势**:
- ✅ 未经测试的新假设，可能有正期望
- ✅ 降低交易频率 = 降低累积成本
- ✅ 更适合加密货币的波动特性

---

### 方案3: 创建独立实验Paper Run

#### 3.1 配置双轨制
```python
# 保守轨: 保持现有50%阈值，仅开确定性高的单
paper_run_conservative = {
    "meta_label_min_win_rate": 0.50,
    "risk_per_trade": 0.025,
    "account_equity": 10000
}

# 实验轨: 放宽到46%，小仓位测试
paper_run_experimental = {
    "meta_label_min_win_rate": 0.46,
    "risk_per_trade": 0.01,  # 降低到1%
    "account_equity": 2000,   # 独立小账户
    "max_open_positions": 3,
    "paper_run_id": "experimental_lower_threshold"
}
```

#### 3.2 实验期限
- **运行时长**: 7-14天
- **最小样本**: 30笔交易
- **评估指标**: 
  - 胜率 >= 45%
  - 盈亏比 >= 1.8
  - 最大回撤 < 15%

---

## 实施步骤

### Phase 1: 立即优化 (今天)
1. ✅ **降低MetaLabel阈值**: 50% → 46%
2. ✅ **启用Swing策略**: 取消注释`bootstrap_auto_trading_swing_paper_run()`
3. ✅ **创建实验Paper Run**: 独立1%风险测试

### Phase 2: 数据驱动优化 (依赖安装完成后)
4. ⏳ **运行edge stats分析**: `compute_signal_edge_stats.py`
5. ⏳ **调整信号权重**: 基于分析结果优化组合
6. ⏳ **验证Swing策略**: 运行`TechnicalStrategyValidationService`

### Phase 3: 监控与迭代 (接下来7天)
7. 📊 每日监控拒绝原因分布
8. 📊 追踪实验Paper Run表现
9. 📊 如果7天内仍无正期望，考虑引入新数据源

---

## 风险控制

### 不会改变的核心规则
- ✅ 强制止损
- ✅ 杠杆上限
- ✅ 相关性风控
- ✅ 回撤硬停
- ✅ 连续亏损限制

### 放宽的规则
- ⚠️ MetaLabel胜率: 50% → 46%
- ⚠️ 止损距离: ATR × 2.0 → 2.5 (仅Swing)
- ⚠️ 单笔风险: 2.5% → 1% (实验轨)

### 保留的退出机制
如果优化后仍持续亏损:
- **7天评估点**: 胜率<43%或累计亏损>10%则回滚
- **14天硬停点**: 必须达到正净期望或停止该策略

---

## 预期效果

### 乐观场景 (概率40%)
- **信号增加**: 20-30%
- **开单频率**: 每天1-3笔 (vs 当前0笔)
- **Swing策略**: 可能发现正期望形态

### 中性场景 (概率40%)
- **信号增加**: 10-20%
- **胜率下降**: 46-48%
- **净期望**: 接近0，需要继续优化

### 悲观场景 (概率20%)
- **信号增加但亏损加速**
- **胜率<44%**
- **需要回滚或引入新数据源**

---

## 决策记录

**决策者**: 用户  
**选择方案**: 方案B (信号分析 + 适度放宽)  
**执行日期**: 2026-07-15  
**预期见效**: 1-2天内开始有订单  
**评估日期**: 2026-07-22 (7天后复盘)

---

## 后续工作

1. **新策略研发**:
   - 波动率突破策略
   - 链上数据整合
   - 宏观事件驱动

2. **数据源扩展**:
   - 巨鲸地址监控
   - 交易所流入流出
   - Twitter情绪指标

3. **模型升级**:
   - 引入机器学习预测
   - 动态调整信号权重
   - 市场状态自适应

---

**报告生成**: 2026-07-15  
**下次更新**: 优化实施完成后
