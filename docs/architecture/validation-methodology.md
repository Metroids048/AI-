# 验证方法论与成本建模设计包

## 文档定位

本文件是 Validation Layer 的方法论细化文档，回答 `domain-and-interfaces-design.md` 中 `BacktestRun.cost_model_ref` / `validation_methodology` / `stress_test_scenarios` 三个字段具体应该装什么。

上游真源：

- [AI_Quant_Research_Platform_完整报告.docx](C:\Users\Windows11\Desktop\量化项目\AI_Quant_Research_Platform_完整报告.docx)
- [AGENTS.md](C:\Users\Windows11\Desktop\量化项目\AGENTS.md)
- [domain-and-interfaces-design.md](C:\Users\Windows11\Desktop\量化项目\docs\architecture\domain-and-interfaces-design.md)

本轮不做：

- 不实现具体的 Deflated Sharpe 计算代码
- 不实现具体的滑点估算算法
- 不接入真实订单簿深度数据

这些留给 Phase 1 实现阶段。本文件只定义方法论边界、必须覆盖的场景与字段口径。

---

## 01 Walk-Forward 滚动验证

- 禁止在同一段历史数据上反复调参再拿同一段数据回测评分（会导致过拟合）
- 标准流程：训练窗口（in-sample）→ 紧邻的验证窗口（out-of-sample）→ 窗口整体向前滚动，重复
- `BacktestRun.sample_split_plan` 记录窗口划分方案（训练窗口长度、验证窗口长度、滚动步长）
- `BacktestRun.validation_methodology` 记录本次实际使用的滚动参数，以及本策略/信号组合历史上被测试过的次数（供 02 节多重检验校正使用）

---

## 02 多重检验偏差校正（Deflated Sharpe Ratio）

- 背景：策略库里有数百个 alpha 和多个技术策略，任意组合搜索出来的"最优组合"大概率是过拟合出来的高分
- 要求：任何进入模拟盘准入评估的 `BacktestRun`，其 `metrics_summary` 中的 Sharpe 必须能换算出 Deflated Sharpe（用"测试过的组合数"扣掉多重检验膨胀的部分），而不是直接使用原始 Sharpe
- `BacktestReport.trials_count` 记录本次评估之前，该策略/信号族历史上被尝试过的参数组合/变体数量
- `BacktestReport.deflated_sharpe` 记录扣除多重检验偏差后的 Sharpe，Validation Layer 的准入门槛（AGENTS.md §4：`Sharpe > 1.0`）应优先参考此字段而非原始 Sharpe
- 具体计算公式与实现留给 Phase 1，本文件只固定契约字段与"必须做"这条硬约束

---

## 03 压力测试场景库

任何策略在进入模拟盘之前，必须至少覆盖以下场景中与其市场/时间范围相关的部分：

- `luna_collapse_2022_05`：2022 年 5 月 LUNA/UST 崩盘，极端流动性枯竭 + 单边暴跌
- `black_thursday_2020_03`：2020 年 3 月 12 日（312），全市场单日巨幅下跌 + 交易所限流/宕机
- `exchange_outage`：交易所 API/WebSocket 长时间不可用，模拟连接中断下策略行为
- `extreme_wick`：极端插针行情（瞬时穿透止损位后迅速回归），检验止损/清算逻辑是否被插针误伤或未能及时触发

`BacktestRun.stress_test_scenarios` 记录本次覆盖了哪些场景标签及对应表现摘要。场景库本身可持续补充，但新增场景不得删除已有场景（避免历史策略的压力测试覆盖率倒退）。

---

## 04 交易成本建模

回测收益必须扣除以下三项成本，缺一不可：

1. **手续费**：按交易所 maker/taker 费率分别核算（同一策略可能既有限价单也有市价单）
2. **滑点**：基于订单簿深度估算，而不是假设零滑点或固定百分比；深度数据来源与估算方法在 Phase 1 实现时确定
3. **资金费率净收支**：永续合约持仓期间的资金费率净收入/净支出必须计入损益，尤其是资金费率套利类策略（本身就是靠这项赚钱），不能只统计价格变动收益

`BacktestReport.total_cost_bps` 统一用 bps（基点）口径汇总上述三项成本，作为 `metrics_summary` 的必填组成部分。没有成本建模的回测结果不得作为模拟盘准入依据。

---

## 05 与 Validation 准入门槛的关系

AGENTS.md §4 的默认门槛（`Sharpe > 1.0` / `Profit Factor > 1.3` / `Max Drawdown < 25%` / `Expectancy > 0`）在接入本文件方法论后，判断口径调整为：

- Sharpe 优先看 `deflated_sharpe`（若尚未计算则回退到原始 Sharpe，但需在 `eligibility_result` 中注明未经多重检验校正）
- 所有收益类指标必须是扣除成本后的净值，不能是毛收益
- 压力测试场景覆盖率不足的策略，即使通过前述门槛，也只能标记为"有条件通过"，需人工审核后才能进入模拟盘（对应 `agent-and-orchestration-design.md` 06 节"模拟盘准入"人工决策边界）

---

## 06 P0 实现边界

P0（本阶段）只做：

- 本文件的方法论文字定义
- `BacktestRun` / `BacktestReport` 契约字段占位

P1 才做：

- Deflated Sharpe 具体公式实现
- 订单簿深度滑点估算实现
- 压力测试场景的历史数据回放实现
