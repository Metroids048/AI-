# 信号融合与二级仓位判定（Ensemble / Meta-labeling）

当前状态（2026-07-03）：本目录已有首版 deterministic service/API，实现的是可审计的
信号投票融合与规则化 meta-label 判定接缝；训练型模型、三重界限法样本生成和在线调参
仍未实现。

已落地代码：

- `service.py`：`EnsembleService`，根据 `SignalVote` 权重生成 `SignalEnsemble`，
  并按置信度、相关性与建议风险倍数生成 `MetaLabel`。
- `apps/api/routers/ensemble.py`：`/api/v1/strategy/ensemble/*` 路由。
- `tests/api/test_signal_ensemble.py`：deterministic service/API 行为测试。

## 定位

本子模块归属 `services/strategy_library`（Strategy Layer），不是独立子域。职责：

1. **信号融合（SignalEnsemble）**：多个策略/alpha 的候选信号，先做相关性矩阵过滤，只保留低相关的子集，再融合为单一交易候选。WorldQuant alpha 在这里只是低权重投票之一，权重由历史验证数据迭代调整，不作为独立策略。
2. **二级仓位判定（MetaLabel）**：对融合后的候选交易，用三重界限法（triple-barrier：止盈/止损/时间限）标注历史样本，训练轻量模型（如逻辑回归）判定"是否下注、下多大仓位"。一级信号只回答方向，本层回答仓位。

契约定义见 `shared/models/signal.py` 的 `SignalEnsemble` / `MetaLabel` / `SignalVote`。架构说明见：

- [domain-and-interfaces-design.md §3.5a/§3.5b](C:\Users\Windows11\Desktop\量化项目\docs\architecture\domain-and-interfaces-design.md)
- [agent-and-orchestration-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\agent-and-orchestration-design.md)
- [execution-risk-review-design.md §03a](C:\Users\Windows11\Desktop\量化项目\docs\architecture\execution-risk-review-design.md)

## 后续实现边界

按 `appendix-b-feature-phasing.md` 的 P1 优先级，资金费率/基差套利底仓策略仍优先服务
Validation Layer 闭环。本目录当前只提供 Strategy Layer 子模块的结构化融合接缝，
后续如引入训练型 meta-label，必须继续复用 `shared/models/signal.py` 的契约，不新增第 7 层。
