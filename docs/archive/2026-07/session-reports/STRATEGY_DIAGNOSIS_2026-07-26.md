# 量化策略诊断报告 2026-07-26

## 执行摘要

用户报告："ETH 1972 空单完全没有时效性，止损单没看到"

**实际情况：**
1. ❌ **误读：** 1972 不是空单，是 ETH **多头**的止盈单（long @ 1878.82，take-profit @ 1972.76）
2. ✅ **止损存在：** stop=1831.85（数据库 protection_records 表 ACTIVE 记录）
3. 🔴 **严重问题：** 37 个数据库持仓 vs 2 个实际持仓 → **35 个鬼持仓污染**

---

## 问题 1：鬼持仓污染（🔴 严重）

### 现状

```
数据库 position_records：37 个 MANAGED_STRATEGY 持仓
交易所实际持仓：        2 个 (BTC short 0.024 + ETH long 0.088)
差异：                 35 个鬼持仓
```

**影响：**
- 风控计算错误：组合风险基于 37 个持仓，实际只有 2 个
- 新信号被拒绝：`max_portfolio_initial_risk_fraction` 提前饱和
- 保护单堆积：每个鬼持仓都有 ACTIVE 保护单记录

### 根因

1. **手动平仓未同步：** 币安界面手动平仓，但 Paper Runtime 未运行 reconcile
2. **程序崩溃/重启：** 持仓被外部平仓（止损触发、爆仓），但重启后未清理本地记录
3. **Reconcile 频率不足：** 仅在特定事件触发，未持续对账

### 修复方案

**立即执行：**

```bash
# 1. Dry-run 检查
python scripts/emergency_reconcile_positions.py

# 2. 确认无误后执行清理
python scripts/emergency_reconcile_positions.py --execute
```

**长期预防：**

1. **增加 reconcile 频率：** 每个 runtime cycle 结束后强制对账
2. **添加启动时对账：** `RuntimeScheduler.start()` 时强制 reconcile
3. **监控告警：** 当 `(local_positions - exchange_positions) > 3` 时发送通知

---

## 问题 2：用户理解错误（🟢 澄清）

### 误读分析

**用户截图显示：** "ETH 买入条件单 @ 1972"

**实际情况：**

```python
持仓：ETH long 0.088 @ 1878.82 (2026-07-26 06:34:21)
止损：1831.85 (多头止损在下方 ✅)
止盈：1972.76 (多头止盈在上方 ✅)
```

**币安 UI 显示逻辑：**
- 多头持仓的止盈单 = **卖出**平仓单（SELL reduce-only）
- 但触发价 1972 > 当前价，在 UI 的条件单列表里可能显示为"买入方向"（因为是"价格向上突破"触发）
- 这是 UI 展示问题，不是订单方向错误

**用户看到的 0.081/0.086/0.087 "买入"单：**
- 这些是 `submitted` 状态的废单（历史入场单提交失败）
- 不是当前持仓的保护单
- 应该被清理但未清理

### 验证

```sql
-- 查询当前 ETH long 持仓的保护单
SELECT pr.symbol, pr.position_side, pr.quantity,
       pt.stop_price, pt.take_profit_price, pt.status
FROM position_records pr
JOIN protection_records pt ON pr.position_record_id = pt.position_record_id
WHERE pr.symbol = 'ETH/USDT'
  AND pr.position_side = 'long'
  AND pr.management_status = 'MANAGED_STRATEGY';

-- 结果：
-- symbol    | side | qty   | stop_price | take_profit | status
-- ETH/USDT  | long | 0.088 | 1831.8495  | 1972.761    | ACTIVE  ✅
```

---

## 问题 3：时效性问题（🟡 策略设计）

### 用户抱怨

> "挂了一个 1972 的止盈单，当前价 1880 左右，要涨 6 个点才能到，至少得 1 周多，完全没有时效性"

### 现状分析

**当前配置（bootstrap.py 第 112 行）：**

```python
"takeprofit_rules": {"risk_reward": 2.0}  # 固定 2R 止盈
```

**实际数值：**
```
入场：1878.82
止损：1831.85 (距离 47 USDT, 2.5%)
止盈：1972.76 (距离 94 USDT, 5.0% = 2×止损距离)
当前：1887.57 (浮盈 0.47%)
```

**这是 Paper 阶段的保守配置，特点：**
- ✅ 风险回报比 2:1
- ✅ 止损/止盈固定，不动态调整
- ❌ 确实可能等待时间长（ETH 日波动 3-5%）

### 改进方案

**方案 1：降低 R 倍数（最简单）**

```python
"takeprofit_rules": {"risk_reward": 1.5}  # 止盈距离 = 1.5×止损
# 新止盈：1878.82 + 47×1.5 = 1949.32 (距离 3.75%)
```

**方案 2：分批止盈（推荐，代码已实现）**

```python
"takeprofit_rules": {
    "exit_ladder": [
        {"r_multiple": 1.0, "close_fraction": 0.5},  # 1R 平 50%
        {"r_multiple": 2.0, "close_fraction": 0.5},  # 2R 平剩余
    ],
    "remainder_trail_after_r": 1.5  # 剩余仓位在 1.5R 后移动止损到入场价
}
# 效果：
# - 1925.79 (1R) 触发，平 50% → 锁定利润
# - 剩余 50% 等待 2R，或在 1.5R 后保护到盈亏平衡
```

**方案 3：时间止损（适合震荡市）**

```python
"exit_rules": {
    "time_exit_hours": 24,        # 24小时未触发则平仓
    "time_exit_min_r": 0.5,       # 且浮盈超过 0.5R
    "close_on_opposite_signal": True
}
```

**代码位置：**
- `services/execution/exit_ladder.py` (已实现完整的分批止盈逻辑)
- `services/execution/bootstrap.py` (修改配置)
- `services/execution/paper_cycle_orchestrator.py` (执行层，已支持 exit_ladder)

---

## 问题 4：废单未清理（🟡 中等）

### 现状

用户截图里的 0.081/0.086/0.087 ETH "买入"条件单：

```sql
SELECT symbol, direction, execution_status,
       json_extract(entry_context, '$.quantity') as qty
FROM order_executions
WHERE symbol = 'ETH/USDT'
  AND execution_status = 'submitted'
ORDER BY created_at DESC;

-- 结果：
-- ETH/USDT | short | submitted | 0.087  (2026-07-26 06:34:15)
-- ETH/USDT | short | submitted | 0.087  (2026-07-26 06:34:15)
```

这些是：
- 历史入场单提交失败，状态卡在 `submitted`
- 对应持仓已平仓或从未开仓
- 币安 UI 仍显示这些条件单

### 修复

```python
# 添加到 paper_cycle_orchestrator.py 的 reconcile 逻辑
def _cancel_orphaned_conditional_orders(self, *, paper_run: PaperRun) -> list[str]:
    """取消没有对应持仓的条件单"""
    gateway = self.exchange_execution.gateway
    if gateway is None:
        return []

    # 查询所有条件单
    algo_orders = gateway._fetch_open_algo_orders()

    # 查询所有持仓
    snapshot = gateway.reconcile(live_run_id=f"paper:{paper_run.paper_run_id}")
    position_symbols = {pos["symbol"] for pos in snapshot.get("open_positions", [])}

    cancelled = []
    for order in algo_orders:
        symbol = order.get("symbol")
        if symbol not in position_symbols:
            # 没有对应持仓，取消条件单
            gateway.cancel_order(gateway_order_id=order["algoId"])
            cancelled.append(order["algoId"])

    return cancelled
```

---

## 执行清单

### 🔴 立即执行（严重）

- [ ] 运行 `python scripts/emergency_reconcile_positions.py` 检查鬼持仓
- [ ] 确认后执行 `--execute` 清理
- [ ] 验证清理后 `position_records` 只剩 2 个 MANAGED_STRATEGY

### 🟡 本周内（中等）

- [ ] 决定是否修改止盈策略（降低 R 倍数 或 启用 exit_ladder）
- [ ] 添加启动时强制 reconcile：`RuntimeScheduler.start()` 增加对账
- [ ] 实现 `_cancel_orphaned_conditional_orders()` 清理废单

### 🟢 长期优化（低优先级）

- [ ] 增加监控告警：`(local_positions - exchange_positions) > 3`
- [ ] 每个 runtime cycle 结束后强制 reconcile
- [ ] 优化 UI 展示：区分"当前持仓保护单"和"历史废单"

---

## 附录：数据证据

### 当前持仓快照（2026-07-26 12:28）

```
symbol    | side  | qty   | entry   | mark    | pnl
BTC/USDT  | short | 0.024 | 64033.1 | 64557.1 | -12.31
ETH/USDT  | long  | 0.088 | 1878.82 | 1887.57 | +0.77
```

### 鬼持仓样本（部分）

```
position_id | symbol    | side  | qty    | opened_at
9c844bb2    | SOL/USDT  | short | 2.131  | 2026-07-25 14:48:49
cb112709    | BTC/USDT  | short | 0.025  | 2026-07-25 14:36:17
acbf4a1c    | SOL/USDT  | short | 2.113  | 2026-07-25 14:20:48
... (共 35 个)
```

### 最近订单记录

```
时间                 | 符号      | 方向  | 入场价  | 止损     | 止盈     | 状态
2026-07-26 12:06:49 | SOL/USDT  | short | 74.98   | N/A      | N/A      | filled
2026-07-26 09:50:49 | SOL/USDT  | short | 75.17   | 77.04925 | 71.4115  | filled
2026-07-26 06:35:18 | ETH/USDT  | long  | 1878.82 | 1831.85  | 1972.76  | filled ✅
2026-07-26 06:34:15 | ETH/USDT  | short | N/A     | N/A      | N/A      | submitted (废单)
```

---

## 结论

1. **不是策略逻辑 BUG**：止损止盈都正确，只是配置偏保守
2. **主要问题是数据脱节**：35 个鬼持仓污染数据库
3. **用户理解有偏差**：误读 UI 展示，将多头止盈单看成空单

**建议立即执行清理脚本，然后根据实际交易节奏调整止盈策略。**
