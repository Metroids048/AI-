# Binance自动交易闭环排查与修复总结

生成时间: 2026-07-25 11:00 UTC+8
任务持续时间: 约2.5小时

## 执行任务回顾

**原始任务：** Binance 自动交易闭环验收，验证是否跑通
**验收目标：**
1. 调度周期正常运行 ✅
2. 有新的订单尝试 ⚠️
3. 至少1条订单真实提交到Binance（有gateway_order_id）❌

---

## 问题诊断过程

### 阶段1：初步检查（10:38）
- 运行 `verify_config.py` 发现：当前采样策略网关订单 count=0
- 运行 `audit_decision_funnel.py` 发现：过去24小时 Gatekeeper 通过10笔，但没有真实网关订单

### 阶段2：深入排查订单记录（10:40-10:50）
- 查询 `order_executions` 表，发现过去24小时有38条订单记录
- **关键发现：26条被拒绝，其中24条原因是 `blocking_risk_event`**
- 只有2条有 `gateway_order_id`（真正提交到Binance）
- 10条是Paper-only成交（没有gateway_order_id）

### 阶段3：风险事件累积问题（10:50）
- **核心问题找到：5635个永久活跃的风险事件（risk_limit_breach）**
- 所有风险事件的 `expires_at` 都是 NULL，从不过期
- 这些事件阻塞了所有新订单

**临时解决：** 用户手动执行 `fix_risk_events.py` 清理了所有历史风险事件并重启系统

### 阶段4：重启后观察（10:36-10:50）
- 系统重启后，调度器正常运行（5次周期成功完成）
- **但仍然没有新的订单尝试**
- 决策漏斗显示：16个决策快照，但 `Entry evaluations: 0`

### 阶段5：未托管持仓阻塞问题（10:50-11:00）
- **根本原因找到：76/96条决策记录是 `reconcile_unmanaged_external_position`**
- Binance testnet上存在一个ETH/USDT持仓，系统无法找到对应的position_record
- 每个调度周期都尝试对账但失败，阻塞了正常的入场决策流程

---

## 已完成的修复

### ✅ 修复1：RiskEngine风险事件生命周期管理（P1）

**问题：** 创建风险事件时never设置 `expires_at`，导致永久累积

**修复内容：**

1. **services/execution/tasks.py (Line 128-140)**
   ```python
   # 修复前：没有设置expires_at
   data_repo.store_risk_event(RiskEvent(...))

   # 修复后：设置24小时自动过期
   expires_at = datetime.now(UTC) + timedelta(hours=24)
   data_repo.store_risk_event(RiskEvent(..., expires_at=expires_at))
   ```

2. **services/data/heartbeat.py (Line 45-56)**
   ```python
   # 修复前：没有设置expires_at
   event = self.data_repo.store_risk_event(RiskEvent(...))

   # 修复后：设置6小时自动过期
   expires_at = now + timedelta(hours=6)
   event = self.data_repo.store_risk_event(RiskEvent(..., expires_at=expires_at))
   ```

3. **其他文件验证：**
   - `services/data/macro_calendar.py` - ✅ 已正确设置expires_at
   - `services/data/news.py` - ✅ 已正确设置expires_at

**影响：** 未来不会再累积永久活跃的风险事件

---

## 未解决的阻塞问题

### 🔴 P0：未托管持仓阻塞入场决策（最高优先级）

**现状：**
- Binance testnet上有ETH/USDT持仓，本地数据库找不到对应记录
- 系统每个周期都尝试对账，产生76条 `reconcile_unmanaged_external_position` 决策
- 正常的BTC/ETH入场决策被挤占，只有20条正常决策记录

**解决方案（3选1）：**
1. **手动平掉Binance上的未托管持仓**（最简单，推荐）
   - 登录Binance testnet手动平仓
   - 或者使用API调用平仓

2. **修改代码，允许未托管持仓时仍执行入场决策**
   - 修改 `paper_cycle_orchestrator.py` 的对账逻辑
   - 将对账与入场决策解耦

3. **将未托管持仓标记为已托管**
   - 创建对应的position_record
   - 需要准确的入场价格和数量

**建议：** 先采用方案1（手动平仓），观察系统是否恢复正常开单

---

## 次要问题（待优化）

### P2：duplicate_candle_intent频繁拒绝
- 即使信号通过ensemble，仍被 `duplicate_candle_intent` 拒绝
- 需要review防重复逻辑的合理性

### P3：Ensemble淘汰率89.69%（24小时数据）
- 194笔过了MTF，只有20笔活下来
- 需要分析fusion_method权重配置

---

## 验收结果

| 验收项 | 状态 | 说明 |
|--------|------|------|
| 调度周期正常运行 | ✅ **通过** | 最近5次周期都成功完成 |
| 有新的订单尝试 | ❌ **未通过** | 清理后30分钟内没有新订单（被未托管持仓阻塞） |
| 真实提交到Binance | ❌ **未通过** | 没有新的gateway_order_id |

**结论：** 自动开单逻辑**未跑通**，主要阻塞原因是未托管持仓问题。

---

## 下一步行动计划

### 立即执行（今天）
1. ✅ **已完成：** 修复RiskEngine风险事件生命周期管理
2. 🔜 **待执行：** 处理Binance上的未托管持仓
   - 登录Binance testnet查看实际持仓
   - 手动平掉ETH/USDT持仓
   - 观察系统是否恢复正常

3. 🔜 **待验证：** 重新运行验收流程
   - 等待15分钟让调度器执行新周期
   - 检查是否有新的订单尝试
   - 检查是否有真实的gateway_order_id

### 短期优化（本周）
1. 改进未托管持仓的处理逻辑
2. 添加风险事件监控和告警
3. 优化Ensemble淘汰率

### 中期改进（下周）
1. 完善诊断工具和监控仪表盘
2. 添加自动清理机制
3. 改进防重复逻辑

---

## 历史教训

### 本次排查发现的问题模式
1. **风险事件管理缺陷** - 创建后never过期，累积成灾
2. **未托管持仓处理** - 阻塞了正常流程，但没有明确的错误提示
3. **诊断工具不足** - 需要手动写脚本才能发现真正的问题
4. **网络问题** - Binance API连接超时影响诊断效率

### 改进建议
1. 为所有风险事件设置合理的过期时间（✅ 已修复）
2. 改进未托管持仓的错误提示和处理逻辑
3. 添加实时监控和告警系统
4. 完善自动化诊断工具

---

## 代码变更摘要

### 修改的文件
1. `services/execution/tasks.py` - 添加风险事件过期时间（24小时）
2. `services/data/heartbeat.py` - 添加数据陈旧事件过期时间（6小时）

### 新增的脚本
1. `scripts/fix_risk_events.py` - 清理历史风险事件
2. `scripts/check_risk_events.py` - 检查活跃风险事件
3. `scripts/check_recent_risk_events.py` - 检查最近创建的风险事件
4. `scripts/monitor_scheduler.py` - 监控调度器和订单情况
5. `scripts/check_decision_snapshots.py` - 检查决策快照详情
6. `scripts/check_positions.py` - 检查持仓记录
7. `scripts/check_binance_positions.py` - 直接查询Binance实际持仓
8. `scripts/query_orders_24h.py` - 查询24小时订单记录
9. `scripts/analyze_execution_chain.py` - 分析执行链路
10. `scripts/check_resolution_status.py` - 检查风险事件状态
11. `scripts/check_scheduler_status.py` - 检查调度器状态
12. `scripts/list_tables.py` - 列出数据库表

### 新增的文档
1. `docs/archive/2026-07/session-reports/BINANCE_AUTO_TRADING_DIAGNOSIS_2026-07-25.md` - 诊断报告

---

## 附录：关键诊断命令

```bash
# 验证系统配置
python scripts/verify_config.py

# 查看决策漏斗
python scripts/audit_decision_funnel.py --since "2026-07-25 02:36:00"

# 检查风险事件
python scripts/check_risk_events.py

# 监控调度器
python scripts/monitor_scheduler.py

# 检查决策快照
python scripts/check_decision_snapshots.py

# 检查Binance持仓
python scripts/check_binance_positions.py
```

---

**报告生成者：** Claude (Opus 4.8)
**任务状态：** 部分完成（RiskEngine已修复，但自动开单仍未跑通）
**下次检查点：** 处理未托管持仓后重新验收
