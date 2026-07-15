# 超激进Paper测试配置 - 最终版本（2026-07-15）

## 配置目标

**核心目标**：在Paper/Testnet模拟盘环境下，最大化订单生成频率以快速积累交易样本数据。

**预期结果**：从当前0单/天提升到5-20单/天，在7-14天内积累100+真实交易样本。

**风险声明**：此配置**仅限Paper/Testnet使用**，绝对禁止用于实盘交易。

## 配置变更汇总

### 1. MetaLabel胜率门槛（bootstrap.py）

```python
# 从 0.50 → 0.46 → 0.42
"meta_label_min_win_rate": 0.42  # 42% × 2R = 0.84期望值
```

**影响**：允许历史胜率42%的策略进场（扣费后期望0.84，低于盈亏平衡但可接受用于采样）

### 2. 方向性策略仓位规则（bootstrap.py AUTO_PAPER_TECHNICAL_RULES）

| 参数 | 保守版 | 积极版 | 超激进版（最终） |
|------|--------|--------|------------------|
| risk_per_trade | 2.5% | 3.5% | **5.0%** |
| max_portfolio_initial_risk_fraction | 15% | 20% | **25%** |
| max_leverage | 25x | 30x | **40x** |
| max_position_fraction | 20% | 25% | **35%** |

### 3. 摆动策略仓位规则（bootstrap.py AUTO_PAPER_SWING_RULES）

| 参数 | 保守版 | 积极版 | 超激进版（最终） |
|------|--------|--------|------------------|
| risk_per_trade | 2.5% | 3.5% | **5.0%** |
| max_portfolio_initial_risk_fraction | 15% | 20% | **25%** |
| max_leverage | 15x | 20x | **30x** |
| max_position_fraction | 15% | 20% | **30%** |

### 4. 风险配置（shared/models/risk.py medium_risk_profile）

| 参数 | 保守版 | 积极版 | 超激进版（最终） |
|------|--------|--------|------------------|
| single_trade_risk_limit | 2.5% | 3.5% | **5.0%** |
| max_symbol_exposure | 20% | 25% | **35%** |
| max_total_exposure | 60% | 80% | **90%** |
| max_open_positions | 6 | 8 | **10** |
| max_leverage | 25x | 30x | **40x** |
| daily_loss_limit | 6% | 10% | **20%** |
| weekly_loss_limit | 10% | 15% | **25%** |
| drawdown_limit | 15% | 20% | **25%** |
| hard_stop_drawdown_limit | 22% | 30% | **40%** |
| consecutive_loss_limit | 6 | 8 | **10** |

### 5. 交易币种范围（services/data/universe.py）

**从Top20缩减到Top10**：

保留：BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX, TRX

移除：HYPE, SUI, TON, HBAR, ONDO, ENA, TAO, FET, RENDER, PEPE

**原理**：聚焦流动性最高的主流币种，提高单币种集中度，减少相关性复杂度。

### 6. 摆动策略启用（bootstrap.py）

```python
# 从 Research模式改为 Paper Run创建模式
def bootstrap_auto_trading_swing_paper_run() -> None:
    _bootstrap_auto_paper_strategy(
        strategy_id=AUTO_PAPER_SWING_STRATEGY_ID,
        # ... 完整配置
    )
```

**影响**：同时运行方向性策略和摆动策略，双轨并行扫描。

## 配置逻辑

### 三层进攻策略

1. **MetaLabel 42%门槛**：允许更多候选信号通过历史胜率筛选
2. **40x杠杆 + 5%风险**：单笔信号的仓位权重最大化
3. **35%单币曝光 + 90%总曝光**：允许2-3个仓位同时持有

### 预期订单频率计算

- **Top10扫描频率**：每15分钟一轮完整扫描
- **每轮候选数**：预期0-3个候选信号
- **通过率**：MetaLabel 42%门槛 × Gatekeeper 22规则 ≈ 15-30%
- **日订单数**：96轮/天 × 1.5候选/轮 × 20%通过率 ≈ **5-20单/天**

## 监控指标

### 立即观察（0-24小时）

- [ ] 系统正常启动，无崩溃
- [ ] Top10扫描日志每15分钟输出
- [ ] 首单开单时间
- [ ] 首个拒绝原因分布

### 短期观察（1-7天）

- [ ] 日均开单数：目标 5-20单
- [ ] 实际胜率 vs 预测胜率（42%）
- [ ] 平均持仓时长
- [ ] 止损触发频率
- [ ] 日最大回撤是否超过20%

### 中期评估（7-14天）

- [ ] 累计交易样本数：目标 100+
- [ ] 观察胜率稳定性
- [ ] 实际扣费后P&L
- [ ] 是否需要重新校准MetaLabel阈值

## 启动命令

```cmd
一键启动.cmd
```

系统将自动加载新配置并开始运行。

## 回滚方案

如果系统出现以下情况，立即回滚：

1. **崩溃/错误**：代码逻辑问题
2. **单日亏损超40%**：风控失效
3. **连续10笔全亏**：信号质量极差

回滚方法：

```bash
git checkout HEAD~1 services/execution/bootstrap.py
git checkout HEAD~1 shared/models/risk.py
git checkout HEAD~1 services/data/universe.py
```

## 下一步行动

1. **运行 `一键启动.cmd`** 启动Paper测试
2. **监控日志** 观察首单生成
3. **7天检查点（2026-07-22）**：评估胜率和P&L
4. **14天检查点（2026-07-29）**：统计显著性评估
5. **根据真实数据重新校准MetaLabel阈值**

## 参考文档

- [ADR-065: Ultra-Aggressive Paper Testing v2](.github/agent/memory/decisions-log.md)
- [超激进配置详解](docs/optimization/2026-07-15-aggressive-paper-config.md)
- [自动交易逻辑报告](docs/analysis/2026-07-15-auto-trading-logic-report.md)
