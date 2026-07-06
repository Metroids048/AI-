# 阶段路线图

说明：

- 这里的 `Phase 0/1/2/3` 是仓库整体阶段，不等同于 `appendix-b-feature-phasing.md` 中的 `P0/P1/P2` tranche 标签。
- 当前仓库状态是 `Phase 0 完成 + 第一批 P1 落地`：治理/设计冻结、统一模型、持久化主链、Binance A 级数据、Validation/Paper/Risk/Review/Agent 首批切片已经进入可执行状态。

## Phase 0

- 治理层
- 总设计包
- 领域与接口设计包
- 其余子设计包
- 统一领域模型与 `/api/v1` 主接口已落地
- 仓库卫生、配置安全、文档可移植性作为 P0 补漏项持续维护

## Phase 1

- 统一领域模型代码化
- FastAPI 六大接口簇骨架
- `BTC/USDT` 永续资金费率/基差套利主线
- 历史回测 -> 样本外 -> 模拟盘准入骨架
- 本阶段首个里程碑止于“模拟盘准入就绪”，不含 live 实盘上线
- 已落地切片包括：策略生命周期持久化、Binance public market ingestion、carry 回测应用服务、严格 Paper/Live promotion evidence、Paper runtime cycle、单租户鉴权、通知 outbox/dispatcher。
- 下一轮 P1 顺序：
  1. Celery Beat / 7x24 调度
  2. 前端管理台补齐
  3. B/C/D 级数据源接入

## Phase 2

- 多交易所网关（OKX/Bybit）与更完整 live runtime 验证
- 完整 Deflated Sharpe / 参数优化 / 压力历史回放
- Prometheus/Grafana runtime smoke、告警和值班演练

## Phase 3

- 训练型 meta-labeling 与更深层策略组合优化
- Reddit/YouTube 等更重 E 级研究源
- 多市场扩展：ETH/SOL/A股/美股/黄金/纳指
