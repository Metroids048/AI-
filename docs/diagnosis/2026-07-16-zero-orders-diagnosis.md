# 系统诊断报告 - 2026-07-16

## 问题总结

跑了一晚上，**225个订单全部被拒绝，0个成交**。发现三个关键问题：

## 问题1：配置未生效 ⚠️

**现象**：
- 订单中的`max_portfolio_initial_risk_fraction`显示`0.15`（应该是`0.25`）
- 手续费成本显示`12-16bps`（应该是`5-6bps`）
- 系统仍在扫描ONDO、SUI、HYPE、PEPE、TAO等**不在Top10列表中的币种**

**原因**：
- 代码文件已修改（bootstrap.py、risk.py、universe.py）
- 但数据库中的配置是旧的，没有重新加载
- 有两批Python进程在运行：
  - 旧进程：2026/7/14启动，运行了1天18小时
  - 新进程：2026/7/15 21:47启动，运行了9小时

**解决方案**：
需要重启系统并重新bootstrap配置到数据库。

## 问题2：净期望值计算错误 🐛

**现象**：
所有被拒订单显示：
```
胜率:57.45% 平均盈:0.00R 平均亏:0.00R 成本:16bps 净期望:-0.0007R
```

**分析**：
- `平均盈:0.00R 平均亏:0.00R`是bug，不是真实值
- 应该是`平均盈:2.0R 平均亏:-1.0R`（假设2:1风险回报比）
- 正确计算应该是：`0.5745 × 2.0 + (1-0.5745) × (-1.0) - 0.0016 = 0.72 - 0.0016 = 0.72R`（正期望）
- 但系统误判为负期望导致拒单

**问题定位**：
`decision_pipeline.py`或`gatekeeper.py`中的期望值计算逻辑有bug。

## 问题3：时间显示问题 🕐

**用户反馈**：
"仓位和订单的时间都是刷新或者打开网页的时间，和真正开单的时间完全不一致"

**原因**：
前端显示的是客户端时间或者某个状态更新时间，而不是数据库中的`created_at`真实时间。

**验证**：
数据库中最新订单的真实时间是`2026-07-16 06:51:50`（上午6点51分），如果前端显示的是其他时间，说明前端时间字段映射错误。

## 立即行动计划

### 步骤1：完全停止所有进程

```powershell
# 停止所有Python进程
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

# 确认没有残留
Get-Process python -ErrorAction SilentlyContinue
```

### 步骤2：清理旧数据库（可选，保留数据）

如果想保留历史数据，跳过此步骤。如果想全新开始：

```powershell
# 备份当前数据库
Copy-Item .local_paper_console.db .local_paper_console.db.backup_20260716

# 删除旧数据库
Remove-Item .local_paper_console.db
```

### 步骤3：重新启动系统

```cmd
一键启动.cmd
```

系统启动后会：
1. 创建新数据库或迁移现有数据库
2. 从`bootstrap.py`重新加载配置
3. 新配置将包含：
   - MetaLabel阈值42%
   - 手续费5-6bps
   - Top10币种列表
   - 组合风险上限25%

### 步骤4：验证配置已生效

```powershell
python -c "
import sqlite3
import json

conn = sqlite3.connect('.local_paper_console.db')
cursor = conn.cursor()

# 检查最新扫描的币种
cursor.execute('''
SELECT DISTINCT symbol 
FROM order_executions 
WHERE created_at >= datetime('now', '-15 minutes')
ORDER BY symbol
''')
symbols = [s[0] for s in cursor.fetchall()]
print('当前扫描币种:', symbols)
print('应该只有10个:', 'BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, BNB/USDT, DOGE/USDT, ADA/USDT, LINK/USDT, AVAX/USDT, TRX/USDT')

# 检查最新订单配置
cursor.execute('''
SELECT entry_context
FROM order_executions
ORDER BY created_at DESC
LIMIT 1
''')
result = cursor.fetchone()
if result and result[0]:
    ctx = json.loads(result[0])
    print(f'\n最新订单配置:')
    print(f'手续费: {ctx.get(\"estimated_round_trip_cost_bps\")}bps (应该是6bps)')
    print(f'组合风险上限: {ctx.get(\"max_portfolio_initial_risk_fraction\")} (应该是0.25)')

conn.close()
"
```

## 需要修复的代码Bug

### Bug位置：期望值计算逻辑

需要检查以下文件：
1. `services/execution/decision_pipeline.py` - `_edge_stats_for_gate()`函数
2. `services/execution/gatekeeper.py` - `net_edge_after_cost`检查逻辑

**问题**：`meta_label_average_win`和`meta_label_average_loss`都是0.00R

**预期行为**：
- 如果使用2R止盈/1R止损，应该是：`avg_win=2.0, avg_loss=-1.0`
- 净期望 = 胜率 × 平均盈 + (1-胜率) × 平均亏 - 手续费成本

### Bug位置：前端时间显示

需要检查：
1. `frontend/admin/src/pages/PaperConsole.jsx`
2. 订单表格的时间字段映射

**预期行为**：
- 显示`order.created_at`（订单创建时间）
- 而不是`Date.now()`或其他客户端时间

## 预期结果（修复后）

重启系统并修复bug后，预期：

1. **扫描范围**：只扫描Top10币种（10个，而不是13个）
2. **拒单率**：显著下降（因为手续费从16bps降到6bps，期望值计算正确）
3. **开单率**：5-20单/天（因为42%阈值 + 正确的期望值计算）
4. **时间显示**：显示真实订单时间，不是页面刷新时间

## 监控指标

重启后24小时内观察：

- [ ] 扫描币种数量 = 10（BTC/ETH/SOL/XRP/BNB/DOGE/ADA/LINK/AVAX/TRX）
- [ ] 拒单原因不再全是`net_edge_after_cost_negative`
- [ ] 有订单成功开仓（`execution_status = 'filled'`）
- [ ] 前端时间显示正确
- [ ] 日志中的配置显示新值（手续费6bps，组合风险25%）

## 下一步

1. 立即执行"立即行动计划"重启系统
2. 如果期望值计算bug仍存在，需要修改代码
3. 如果前端时间显示bug仍存在，需要修改前端代码
