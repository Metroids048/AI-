# 7x24小时自动交易逻辑详细报告

**生成日期**: 2026-07-15  
**当前运行状态**: Binance Testnet模拟盘已打通  
**分析目标**: 详细说明自动开平单的逻辑设计、量化策略、配置参数

---

## 一、自动交易架构总览

### 1.1 核心设计原则
- **Fail-closed**: 所有不确定情况默认拒绝,不放行
- **Risk-first**: 风控优先于收益,22条门禁规则
- **Multi-timeframe**: 4h方向→1h状态→15m入场→1m保护
- **Cost-aware**: 所有策略必须扣除真实手续费和滑点后仍有正期望

### 1.2 自动交易流程图

```
RuntimeScheduler (每分钟触发)
    ↓
PaperRuntimeService.run_cycle()
    ↓
├─ 账户权益同步 (从Testnet快照)
├─ Testnet仓位对账 (平掉本地幽灵仓位)
├─ 保护性管理 (1m K线检查止损/止盈)
└─ 扫描Top20 symbols
    ↓
    对每个symbol:
    ├─ 数据新鲜度检查
    ├─ DecisionPipeline评估
    │   ├─ 8个技术指标生成信号
    │   ├─ SignalEnsemble加权融合
    │   ├─ MetaLabel二次过滤 (历史胜率>50%)
    │   └─ LLM Decision Veto (可选)
    ├─ PaperSignalGenerator生成订单请求
    ├─ ExecutionGatekeeper 22条风控门禁
    └─ 通过后镜像到Binance Testnet
