# Binance自动交易闭环诊断报告

生成时间: 2026-07-25 10:50 UTC+8

## 问题汇总

### ✅ 已解决
1. **风险事件累积问题** - 5635个永久活跃的risk_limit_breach事件已清理

### 🔴 当前阻塞问题

#### P0 - 未托管持仓阻塞入场决策（最高优先级）
**现象：**
- 过去1小时有96条决策记录，其中76条是`reconcile_unmanaged_external_position`
- 系统不断尝试对账Binance上的ETH/USDT持仓，但因"no exact managed position identity"而失败
- 正常的入场决策只有20条，大部分被`duplicate_candle_intent`或其他原因拒绝

**根因：**
Binance testnet上存在一个ETH/USDT持仓，本地数据库中无法找到匹配的position_record，系统将其标记为UNMANAGED_EXTERNAL_POSITION，每个调度周期都尝试对账但失败。

**影响：**
- 阻塞了正常的BTC/ETH入场决策流程
- 调度器资源被对账逻辑占用
- 即使风险事件清理后，仍然没有新的真实开单

**解决方案（3选1）：**
1. **手动平掉Binance上的未托管持仓**（最简单）
2. 修改代码，允许系统在有未托管持仓时仍然执行入场决策
3. 将Binance上的持仓标记为已托管（需要创建对应的position_record）

### 🟡 次要问题

#### P1 - RiskEngine风险事件生命周期管理缺陷
**现象：**
- 创建风险事件时never设置expires_at
- 导致累积了5635个永久活跃的风险事件

**解决方案：**
修改`services/execution/risk.py`中创建风险事件的逻辑，为每个风险事件设置合理的过期时间。

#### P2 - duplicate_candle_intent频繁拒绝
**现象：**
- 即使有信号通过ensemble阶段，仍然被duplicate_candle_intent拒绝

**可能原因：**
- 同一根K线上多次产生相同方向的信号
- 防重复逻辑过于严格

#### P3 - Ensemble淘汰率89.69%（来自24小时数据）
**现象：**
- 194笔过了MTF，只有20笔活下来

**需要分析：**
- SignalEnsembleService的fusion_method权重配置
- 对比通过和被淘汰的信号强度分布

## 验收目标回顾

| 目标 | 状态 | 证据 |
|------|------|------|
| 调度周期正常运行 | ✅ | 最近5次周期都成功完成 |
| 有新的订单尝试 | ⚠️ | 清理后30分钟内仍然没有新订单 |
| 至少1条订单真实提交到Binance | ❌ | 没有gateway_order_id |

## 建议的下一步操作（按优先级）

1. **立即执行：** 检查并处理Binance上的未托管持仓
   - 如果网络恢复，运行 `python scripts/check_binance_positions.py`
   - 如果有未托管持仓，手动平掉

2. **观察：** 等待下一个调度周期（15分钟），看是否有新的入场决策和订单

3. **修复：** RiskEngine风险事件生命周期管理
   - 为risk_events设置合理的expires_at
   - 添加自动清理机制

4. **优化：** Ensemble阶段淘汰率过高问题
   - 分析权重配置
   - 调整阈值

## 历史教训

本次排查发现的核心问题模式：
1. **风险事件管理缺陷** - 创建后never过期，累积成灾
2. **未托管持仓处理** - 阻塞了正常流程，但没有明确的错误提示
3. **诊断工具不足** - 需要手动写脚本才能发现真正的问题

建议后续：
- 添加风险事件自动过期机制
- 改进未托管持仓的处理逻辑
- 完善监控和告警
