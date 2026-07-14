# AI Quant Research Platform

本仓库用于实现研究报告定义的 `AI Quant Research Platform`。

当前状态：

- 已建立项目治理与记忆体系
- 已将研究报告约束固化进 [AGENTS.md](AGENTS.md)
- 已完成平台前期准备包与开发前设计收敛索引
- 后续所有代码实现将严格按六层架构推进

当前语义：

- 仓库当前状态：`Phase 0 完成 + 第一批 P1 落地`
- `appendix-b-feature-phasing.md` 中的 `P0/P1/P2` 是实现 tranche 标签，不等同于仓库 phase
- 第一条真实开发主线固定为：`BTC/USDT` 永续 -> 资金费率/基差套利 -> 历史回测 -> 样本外 -> 模拟盘准入

设计入口：

- [设计真源索引与对账总表](docs/architecture/design-source-index.md)

主真源：

- [AI_Quant_Research_Platform_完整报告.docx](AI_Quant_Research_Platform_完整报告.docx)
- [AGENTS.md](AGENTS.md)

项目记忆：

- [project-memory.md](.github/agent/memory/project-memory.md)
- [decisions-log.md](.github/agent/memory/decisions-log.md)
- [task-history.md](.github/agent/memory/task-history.md)

开发与测试：

- 单元测试使用 SQLite，数据库文件写入 `.local/test-runtime/`（可安全删除；`python scripts/clean_test_artifacts.py` 清理 7 天前的残留）

第一阶段目标：

- 建立统一仓库骨架
- 定义领域模型、任务对象、风险对象
- 搭建 FastAPI API 主干
- 为 Validation / Execution / Review 预留清晰边界
- 不把 live 实盘作为第一实现里程碑

已完成的设计包：

- [设计真源索引与对账总表](docs/architecture/design-source-index.md)
- [平台总设计包](docs/architecture/platform-master-design.md)
- [领域与接口设计包](docs/architecture/domain-and-interfaces-design.md)
- [数据与接入设计包](docs/architecture/data-and-ingestion-design.md)
- [Agent 与任务编排设计包](docs/architecture/agent-and-orchestration-design.md)
- [执行 / 风控 / 复盘设计包](docs/architecture/execution-risk-review-design.md)
- [产品规格（上层定位）](docs/product/product-spec.md)
- [功能清单总表（上层总表）](docs/product/feature-catalog.md)
- [产品需求文档（开发验收）](docs/product/prd.md)
- [模块功能清单（字段级承接）](docs/product/module-feature-catalog.md)
- [阶段路线图](docs/roadmap/phase-roadmap.md)
- [环境与配置规范](docs/ops/environment-and-config.md)
- [前期准备交付清单](docs/ops/delivery-checklist.md)

研究输入边界：

- [策略库/笔记.docx](<策略库/笔记.docx>) 只作为研究素材池，不作为正式策略真源
- 任何来源都必须先进入 `StrategyIdea -> StrategyDraft -> StrategyContract`，再进入回测与验证

---

## 退出策略验证框架（2026-07-14 交付）

**状态**: ✅ 生产就绪 | **Git Commit**: `c8637b0`

### 快速开始

```bash
# 对比 Fixed 2R vs ExitLadder（最新 90 天）
python -m scripts.compare_exit_policies_cli --days 90

# 回归验证（冻结 2026-07-12 配置）
python -m scripts.compare_exit_policies_cli \
  --entry-baseline frozen-2026-07-12 \
  --end-at 2026-07-12T08:00:00 \
  --reuse-stored-data \
  --days 90
```

### 核心能力

- ✅ **退出策略 A/B 对比**：固定 entry 配置，隔离 exit 策略的净效应
- ✅ **冻结历史基线**：`frozen-2026-07-12` 回归验证（复现审计方向）
- ✅ **修复预存在崩溃 bug**：`HistoricalMarketDataView.get_latest_market_extras` 缺失导致所有回放工具崩溃
- ✅ **完整测试覆盖**：388 passed（新增 6 个 exit-policy 专项测试）

### 关键发现

| 策略 | Signals | Net Expectancy | Profit Factor | Max Drawdown |
|---|---|---|---|---|
| **Fixed 2R** | 1057 | **+0.001542** ✅ | 1.0910 | 0.6062 |
| **ExitLadder** | 437 | **-0.001244** ❌ | 0.8364 | 1.7497 |

**结论**: Fixed 2R 净预期为正，ExitLadder 为负 → **不建议启用 ExitLadder 自动执行**

### 文档

- 📖 **技术架构**: [docs/technical-validation-framework.md](docs/technical-validation-framework.md)（完整设计 + 模块 8-15 指引）
- 📖 **运维手册**: [docs/exit-policy-validation-runbook.md](docs/exit-policy-validation-runbook.md)（日常操作 + 故障排查）
- 📖 **交付报告**: [docs/DELIVERY-REPORT.md](docs/DELIVERY-REPORT.md)（验收结果 + 风险评估）

### 代码入口

- 核心 API: [services/validation/technical_replay.py](services/validation/technical_replay.py) → `compare_exit_policies()`
- 通用 CLI: [scripts/compare_exit_policies_cli.py](scripts/compare_exit_policies_cli.py)
- 测试套件: [tests/services/test_technical_strategy_validation.py](tests/services/test_technical_strategy_validation.py)

---
