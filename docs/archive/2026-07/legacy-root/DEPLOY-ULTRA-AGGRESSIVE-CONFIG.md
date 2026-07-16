# 超激进Paper测试配置 - 部署就绪

## ✅ 配置已完成

所有配置已成功部署到代码：

### 1. MetaLabel胜率门槛：42%
- `services/execution/bootstrap.py` 第79行和第140行
- 方向性策略和摆动策略均已设置

### 2. 仓位规则：超激进档位
- **方向性策略**：5%风险 + 40x杠杆 + 35%单币曝光
- **摆动策略**：5%风险 + 30x杠杆 + 30%单币曝光
- 位于 `services/execution/bootstrap.py` 第106-114行和第162-170行

### 3. 风险配置：超激进风控
- **单笔风险**：5%
- **单币曝光**：35%
- **总曝光**：90%
- **最大杠杆**：40x
- **日亏损限制**：20%
- **最大持仓数**：10个
- 位于 `shared/models/risk.py` 第50-60行

### 4. 交易币种：Top10主流币
- BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX, TRX
- 位于 `services/data/universe.py`
- 移除了HYPE, SUI, TON, HBAR, ONDO, ENA, TAO, FET, RENDER, PEPE

## 🎯 预期效果

- **日订单数**：5-20单（当前0单）
- **数据积累**：7-14天内获得100+真实交易样本
- **评估目标**：观察真实胜率vs预测42%，收集真实扣费后P&L数据

## 🚀 启动命令

```cmd
一键启动.cmd
```

系统将自动：
1. 启动PostgreSQL数据库（端口8016）
2. 启动FastAPI后端（SQLite模式）
3. 启动前端开发服务器
4. 开始扫描Top10币种，每15分钟一轮
5. 根据新的42%阈值和超激进风控生成订单

## 📊 监控要点

### 立即观察（0-24小时）
- [ ] 系统正常启动无崩溃
- [ ] Top10扫描日志每15分钟输出
- [ ] 首单开单时间（预期24小时内）
- [ ] 拒绝原因分布（通过日志观察）

### 短期观察（1-7天）
- [ ] 日均开单数达到5-20单目标
- [ ] 实际胜率接近42%预测
- [ ] 日最大回撤不超过20%
- [ ] 止损触发频率是否合理

### 中期评估（7-14天）
- [ ] 累计交易样本数≥100
- [ ] 观察胜率是否稳定在42%附近
- [ ] 计算实际扣费后净P&L
- [ ] 决定是否需要重新校准MetaLabel阈值

## ⚠️ 重要提醒

1. **此配置仅限Paper/Testnet使用**，绝对禁止用于实盘交易
2. **这是数据采样配置**，不是盈利配置，预期扣费后略亏但可接受
3. **目标是快速积累样本**，用真实交易数据验证/校准模型
4. **7天检查点**：2026-07-22评估是否需要调整
5. **14天检查点**：2026-07-29评估统计显著性

## 📁 相关文档

- **完整配置说明**：`docs/optimization/2026-07-15-ultra-aggressive-final-config.md`
- **决策记录**：`.github/agent/memory/decisions-log.md` ADR-065
- **项目记忆**：`.github/agent/memory/project-memory.md`
- **自动交易逻辑**：`docs/analysis/2026-07-15-auto-trading-logic-report.md`

## 🔄 回滚方案

如果出现以下情况，立即回滚：

1. **系统崩溃/严重错误**
2. **单日亏损超过40%**（远超20%限制）
3. **连续10笔全部亏损**（信号质量极差）

回滚命令：
```bash
git checkout HEAD~3 services/execution/bootstrap.py
git checkout HEAD~3 shared/models/risk.py
git checkout HEAD~3 services/data/universe.py
```

## ✨ 下一步

**现在就运行 `一键启动.cmd` 开始测试！**

系统将自动加载所有新配置并开始Paper交易测试。
