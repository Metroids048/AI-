<!-- ai-quant-research-platform: v0.1 -->
# AI Quant Research Platform

## Canonical Source

- 主架构真源： [AI_Quant_Research_Platform_完整报告.docx](C:\Users\Windows11\Desktop\量化项目\AI_Quant_Research_Platform_完整报告.docx)
- 本仓库中的一切实现、拆分、命名、阶段规划，都必须优先服从这份报告。
- 如代码实现与报告冲突：先修正实现方案，再更新代码；不得擅自降级为“简化版交易工具”。

## Product Identity

- 这不是“AI 帮我赚钱”的荐股或跟单工具。
- 这是一个 AI 驱动的量化研究平台，目标是持续生成、验证、淘汰、迭代交易策略。
- AI 的职责是研究、规则化、编码、分析、复盘与知识沉淀，不是主观预测涨跌。

## Non-Negotiables

1. 所有交易想法必须先规则化，再进入回测。
2. 所有策略必须经过：历史回测 -> 模拟盘 -> 小资金实盘。
3. 风控优先级永远高于收益。
4. Review Layer 不是附属报表，而是一级核心模块。
5. WorldQuant、GitHub、论文、A 股系统都是策略来源，不是平台主干。
6. 不允许跳过 Validation Layer 直接接 Execution Layer。

## Six-Layer Architecture

### 1. Data Layer

- A级市场数据：OHLCV、Volume、ATR、VWAP、EMA、Bollinger、RSI、MACD、ADX、Funding Rate、OI、Long/Short Ratio、Liquidation、Order Book
- B级宏观数据：FOMC、CPI、PPI、非农、利率决议、GDP、PMI、ETF 进展
- C级新闻数据：金十、Reuters/Bloomberg RSS、CoinDesk/The Block/Decrypt、A股新闻、SEC Filing
- D级社媒数据：重点账号事件流
- E级研究数据：GitHub、论文、Reddit、Telegram、YouTube、WorldQuant、本地 A 股系统

### 2. Strategy Layer

- Strategy Library 是平台长期核心资产。
- 禁止把策略逻辑散落在临时脚本中。
- 每条策略都必须具备：
  - `strategy_id`
  - `source`
  - `core_thesis`
  - `market`
  - `timeframe`
  - `market_regime`
  - `entry_rules`
  - `exit_rules`
  - `stoploss_rules`
  - `takeprofit_rules`
  - `position_rules`
  - `risk_level`
  - `backtest_status`
  - `paper_status`
  - `live_status`
  - `failure_reasons`
  - `iteration_history`

### 3. AI Agent Layer

- `Strategy Agent`
- `Coding Agent`
- `Backtest Agent`
- `Optimization Agent`
- `Research Agent`
- `News Agent`
- `Twitter Agent`
- `Telegram Agent`
- `Risk Agent`
- `Review Agent`

所有 Agent 必须通过结构化对象和任务通信，不得用“随手写文件”串联流程。

### 4. Validation Layer

- 历史回测
- 参数优化
- 样本外验证
- 模拟盘
- 小资金实盘

默认门槛：

- `Sharpe > 1.0`
- `Profit Factor > 1.3`
- `Max Drawdown < 25%`
- `Expectancy > 0`

不达标策略不得进入模拟盘。

### 5. Execution Layer

- Execution Engine
- Risk Engine

硬性规则：

- 没有止损的单子拒绝执行
- 禁止 Martingale
- 禁止因主观感觉人工加仓
- 禁止取消机器人止损

### 6. Review Layer

- 每日复盘
- 失效模式识别
- 策略暂停/调参/淘汰建议
- `failure_reasons` 与 `iteration_history` 回写
- 失败知识沉淀，供 Research/Strategy Agent 复用

## Required Tech Stack

- Python 3.11+
- CCXT
- Freqtrade
- Backtrader / VectorBT
- PostgreSQL
- Redis
- FastAPI
- Celery + Redis
- Docker Compose
- React + Tailwind
- Claude API
- LangChain / LlamaIndex

## Initial Market Scope

- 第一阶段主市场：`BTC/USDT` 永续
- 数据模型从第一天起必须支持未来扩展到：
  - `ETH`
  - `SOL`
  - `A股`
  - `美股`
  - `黄金`
  - `纳指`

## Target Repository Shape

```text
apps/
  api/
frontend/
  admin/
services/
  data/
  strategy_library/
  agents/
  validation/
  execution/
  review/
research_source/
  worldquant_adapter/
docs/
tests/
```

## Working Rules For AI Collaborators

1. 开工前先读：
   - `AGENTS.md`
   - `.github/agent/memory/project-memory.md`
   - `.github/agent/memory/decisions-log.md`
   - `.github/agent/memory/task-history.md`
2. 任何新增模块都必须说明它属于哪一层。
3. 任何代码改动都要说明它服务于哪一段研究闭环。
4. 不得把“先跑起来”当理由绕过风控、验证、复盘层。
5. 完工后必须更新记忆文件。

## Current Phase

- 当前阶段：`Phase 0 - 平台骨架与统一模型`
- 当前优先级：
  1. 全局项目配置
  2. 项目记忆体系
  3. 统一领域模型与仓库骨架
  4. API 与任务流
  5. 数据、验证、执行、复盘实现

