# AI Quant 策略库

本目录是当前项目的人工可读策略库，也是轻量 RAG 的优先检索来源。

## 使用边界

- 这里不是投资建议，也不保证盈利。
- 任何策略必须先规则化，再进入 Backtest；不得从 Markdown 直接进入 Execution。
- 进入模拟盘之前必须有 BacktestRun、GateDecision、PaperRun 和 Gatekeeper 证据。
- LLM 只能做分类、检索、否决、复盘、研究辅助，不能直接生成方向、价格、仓位。
- GPL/AGPL 项目只做蒸馏研究，不复制运行时代码。

## 当前文件

- `00_当前系统策略与开平单逻辑.md`：系统现有 carry + technical 双通道。
- `01_外部策略来源索引.md`：外部项目、课程/文档来源与可借鉴内容。
- `02_ABU策略因子蒸馏.md`：ABU 研究资产蒸馏，研究-only。
- `03_加密货币策略候选库.md`：后续可规则化、回测、模拟盘的候选策略。
- `04_风险控制与禁用清单.md`：开平单、仓位、LLM、第三方接入的硬风控。
- `05_ABU策略组件索引.md`：ABU 买入/卖出/滑点/裁判/评价组件的研究-only 索引。
- `06_GitHub生态补充调研.md`：2026-07-12 GitHub 补充搜索、许可证核验和优化路线映射。

## 入库流程

1. `StrategyIdea`：记录来源、假设、市场、周期、适用状态。
2. `StrategyDraft`：把想法转成入场、出场、止损、止盈、仓位规则。
3. `StrategyContract`：进入平台统一策略模型。
4. `BacktestRun`：历史回测、样本外、成本、压力场景。
5. `PaperRun`：币安 Testnet / Paper 自动运行。
6. `ReviewReport` / `FailureRecord`：复盘、失效原因、迭代历史回写。
