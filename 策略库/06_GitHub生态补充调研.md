# GitHub 生态补充调研

> 固定截面：2026-07-12。方法：逐个执行五组 GitHub repository search，随后核对默认分支 README、仓库许可证元数据和最近 commit。GitHub CLI 未登录，使用公开 API；结果不代表未来状态。

## 搜索结果摘要

| 搜索词 | 结果判断 |
|---|---|
| `crypto trading bot LLM agent` | 项目较多，混入钱包工具、提示词库和无许可证复刻；筛出 Lumen |
| `LLM RAG quantitative trading` | 筛出 HydraQuant、RiverFlow-Apex；其余多为无许可证演示 |
| `crypto perpetual funding arbitrage bot` | 许可证明确者很少；筛出 basis-funding-arbitrage-bot |
| `multi-agent hedge fund LLM` | 多数为同名教学复刻；筛出 hedge-fund-committee |
| `technical analysis signal ensemble crypto backtest` | 精确查询结果为 0，不虚构候选 |

## 已知补充来源核验

| 项目 | 截面证据 | 结论 |
|---|---|---|
| Superalgos | Apache-2.0；默认分支 `master`；commit `9f0fb59edd4b` | 分阶段策略生命周期和插件市场值得吸收；只吸收思想，不引入 Node.js runtime |
| Jesse | MIT；`96110dbc9a83` | 多周期策略形态可用于 4h/15m 分工设计；本地资产已摄取 |
| NautilusTrader | LGPL-3.0；`c28b1335c95a` | 事件驱动一致性和适配器状态值得研究；仅蒸馏 |
| Qlib | MIT；`d5379c520f66` | 实验与数据集管理可支持 MetaLabel 样本外验证 |
| vectorbt | API 为 NOASSERTION；`bf7aff6d081f` | 本地记录按 Apache-2.0 管理，但正式复用前需再次核验仓库内许可证边界 |
| OpenBB | API 为 NOASSERTION；`1c7489314029` | 多许可证/目录边界复杂，平台按 AGPL 受限策略只做蒸馏研究 |

## 五个新候选

### Lumen

- 链接/许可证：[wajdan121/lumen](https://github.com/wajdan121/lumen)，MIT；最近 commit `4ced6e991c79`（2026-07-08）。
- 可吸收：LLM 技术/新闻 Agent 外围的确定性风险过滤、组合账本与 Binance Testnet 隔离。
- 平台映射：AI Agent、Execution、Review。
- 边界：小型新项目，先作为研究证据；不把 Agent 方向判断接入执行主链。

### HydraQuant

- 链接/许可证：[ymcbzrgn/HydraQuant](https://github.com/ymcbzrgn/HydraQuant)，GPL-3.0；`74efbdf42931`（2026-05-31）。
- 可吸收：证据优先的信号引用、RAG 类型分类、风险学习记录。
- 平台映射：AI Agent、Strategy、Review。
- 边界：`distilled_research_only`，不得复制 Freqtrade/GPL runtime。

### basis-funding-arbitrage-bot

- 链接/许可证：[MRowhani/basis-funding-arbitrage-bot](https://github.com/MRowhani/basis-funding-arbitrage-bot)，MIT；`3ee9448925e9`（2026-06-14）。
- 可吸收：现货/永续双腿状态、basis 退出和故障恢复的建模方式。
- 平台映射：Strategy、Execution、Review。
- 边界：Rust 技术栈不接入；仅比较状态机和对账机制。

### hedge-fund-committee

- 链接/许可证：[HenryLinyy/hedge-fund-committee](https://github.com/HenryLinyy/hedge-fund-committee)，MIT；`5334a5581773`（2026-06-28）。
- 可吸收：Bull/Bear 辩论、独立风险审计、PM sign-off 的复盘流程。
- 平台映射：Research Agent、Risk Agent、Review Agent。
- 边界：明确为 research-only，任何 trader call 都不能形成订单。

### RiverFlow-Apex

- 链接/许可证：[Arjo216/RiverFlow-Apex](https://github.com/Arjo216/RiverFlow-Apex)，MIT；`e4cac5ac1989`（2026-03-14）。
- 可吸收：PgVector 证据检索、共识关口和不可变决策审计。
- 平台映射：AI Agent、Validation、Review。
- 边界：README 所称“proprietary”机制只作概念参考；不采纳自动经纪商执行路径。

## 排除记录

- `nirholas/agenti`：核心是 Agent 钱包与支付，不是量化研究闭环，且 API 许可证为 NOASSERTION。
- `Crypto-Data-API/cryptodataapi-prompt-library`：提示词集合，不提供可验证策略/风控机制。
- 多个 `ai-hedge-fund` 同名仓库：无许可证、低活跃证据或明显为教学复刻，默认 `metadata_only`，不进入正式候选。
- 无许可证 funding bot：即使描述相关，也不进入正式资产。

## 与优化路线图的对应关系

| 优化项 | 可参考来源 | 可吸收机制 |
|---|---|---|
| 缠论客观算法 | 暂无合格直接来源 | 继续先定义无流派歧义的客观规则 |
| 4h/15m 信号子集分工 | Jesse | 多周期策略输入与独立周期职责 |
| MetaLabel 样本外验证 | Qlib、vectorbt | 数据集切分、实验记录、参数网格 |
| 组合相关性与净敞口 | Lumen、NautilusTrader | 组合账本、事件状态和风险过滤 |
| Review Agent LLM 接入 | hedge-fund-committee、HydraQuant | 风险审计、证据引用、角色化复盘 |
| RAG 向量化 | RiverFlow-Apex、HydraQuant | PgVector 检索、证据链和检索分类 |
| Regime 过滤 | vectorbt、Qlib | 多市场状态样本外实验 |
| 订单簿/OI 仓位约束 | NautilusTrader、Hummingbot | 适配器状态、库存与流动性风险 |
