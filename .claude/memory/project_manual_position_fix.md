---
name: manual-position-drawdown-fix
description: 手动持仓不再污染策略回撤计算，三处独立路径已统一修复
metadata:
  type: project
---

## 背景

2026-07-25发现严重bug：手动开的ETH持仓（0.086 short，浮亏-110 USDT）被计入策略权益，导致策略回撤(drawdown_pct)虚高，触发错误的风控拒单。更严重的是，`hard_drawdown_limit`(20%)面临误触发风险，会强制平掉策略持仓并永久锁死整个run。

## 修复方案

新增`services/execution/account_equity.py::resolve_manual_position_pnl()`函数，统一处理手动持仓识别和PnL排除逻辑。三处独立的drawdown判断路径全部改用这个函数：

1. **`services/execution/tasks.py::risk_profile_sweep`** - 后台风控扫描（每分钟）
2. **`services/execution/paper_signal.py::_build_risk_state`** - 信号生成前风控预检（每个调度周期）
3. **`services/execution/paper_cycle_orchestrator.py::_is_hard_drawdown_locked`** - 硬回撤锁定检查（每个调度周期，最严重）

核心逻辑：
```python
manual_pnl = resolve_manual_position_pnl(
    paper_run=paper_run,
    execution_repo=self.execution_repo,
    gateway=self.gateway,
)
strategy_equity = raw_account_equity - manual_pnl
```

## 验证结果

**间接验证（历史订单回测）**：
- 找到2026-07-25 09:04:47被drawdown拒绝的ETH/USDT short订单
- 旧逻辑：account_equity=4188.05, drawdown=26.71%（超过25%限制）→ 拒绝
- 新逻辑：排除手动持仓-110后，strategy_equity=4298.05, drawdown=24.79% → **通过**
- ✅ 证明修复有效

**系统健康验证**：
- ✅ 调度器重启后连续8个周期（15:00-15:17）全部正常完成
- ✅ 策略状态保持`running`，未被hard_drawdown误锁定
- ✅ 当前回撤9.53%，在安全范围内

**待真实交易验证**：
- 等待下一次真实开仓订单触发，确认`evaluated_risk_state`里的drawdown值已正确排除手动持仓
- 监控脚本运行中（scripts/monitor_live_execution.py）

## Why

手动持仓的浮亏是"账户层面"的损失，不是"策略层面"的风险。如果把它计入策略回撤，会导致：
1. 策略在盈利时被错误拒单（实际策略回撤远低于阈值）
2. hard_drawdown误触发，强制平掉健康的策略持仓并锁死run（需要手动重置，无自动恢复）

## How to apply

**在任何需要计算策略回撤的地方**（无论是实时判断还是后台扫描），都必须先调用`resolve_manual_position_pnl`排除手动持仓影响，再计算drawdown。

**不要**直接用`paper_metrics_summary['account_equity']`或`gateway.sync_account()`返回的原始equity——这些值包含手动持仓，会污染策略风控。

**异常处理**：`resolve_manual_position_pnl`内部已有fail-safe设计，如果`gateway.reconcile()`失败（网络/API错误），会返回0.0（假设无手动持仓），不会阻塞调度周期。

## 相关决策

- [[reduce-only-rejected-not-a-bug]] - ReduceOnly拒绝是2周期防抖的正常代价，不是bug
- [[reconcile-performance-consideration]] - 每个周期最多调用3次reconcile()，如果耗时持续超120秒，考虑缓存优化
