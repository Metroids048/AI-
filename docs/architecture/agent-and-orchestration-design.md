# Agent 与任务编排设计包

## 文档定位

本文件定义多 Agent 分工、输入输出契约、任务编排原则与调度边界。

---

## 01 Agent 设计原则

- 一个 Agent 一类职责
- Agent 只通过结构化对象协作
- Agent 不直接越权更改核心状态
- Agent 输出必须可审核

---

## 02 Agent 清单

### 2.1 Strategy Agent

- 输入：`StrategyIdea`
- 输出：`StrategyDraft`
- 职责：将研究假设规则化

### 2.2 Coding Agent

- 输入：`Strategy` / `StrategyVersion`
- 输出：`StrategyCodeArtifact`
- 职责：将策略结构转为可执行代码工件

### 2.3 Backtest Agent

- 输入：`StrategyCodeArtifact` / `BacktestRun`
- 输出：完成后的 `BacktestRun`
- 职责：执行历史回测并产出结果

### 2.4 Optimization Agent

- 输入：`StrategyCodeArtifact` / `OptimizationRun`
- 输出：完成后的 `OptimizationRun`
- 职责：执行参数优化

### 2.5 Research Agent

- 输入：外部研究源 / `IngestionJob`
- 输出：`StrategyIdea`
- 职责：发现新研究线索

### 2.6 News Agent

- 输入：新闻源记录
- 输出：新闻风险标签 / `RiskEvent`

### 2.7 Twitter Agent

- 输入：社媒事件记录
- 输出：社媒风险标签 / `RiskEvent`

### 2.8 Telegram Agent

- 输入：Telegram 信号记录
- 输出：结构化信号统计记录 / `StrategyIdea`

### 2.9 Risk Agent

- 输入：运行状态、风险事件、执行日志
- 输出：风险诊断 / `RiskEvent`

### 2.10 Review Agent

- 输入：回测/模拟盘/实盘/风险结果
- 输出：`ReviewReport` / `FailureRecord`

### 2.11 Decision Veto Agent

- 输入：`SignalEnsemble`、`MetaLabel`、近期 `RiskEvent`
- 输出：`veto_decision`（`veto: bool` + `veto_reason`）
- 职责：在信号融合与二级仓位判定完成之后、执行前做一票否决判断；不输出方向/仓位/价格，详见 `execution-risk-review-design.md` 03a
- 越权限制：不得替代 Risk Engine 的硬性拒绝规则，也不得直接下单

---

## 03 AgentTask 统一模型

所有 Agent 任务统一由 `AgentTask` 承载。

最少应包含：

- `agent_task_id`
- `agent_type`
- `task_type`
- `input_ref`
- `output_ref`
- `task_status`
- `priority`
- `scheduled_at`
- `started_at`
- `finished_at`
- `error_summary`

任务状态建议：

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `blocked`

---

## 04 编排主链路

### 4.1 研究闭环主任务链

1. 捕获 `StrategyIdea`
2. 生成 `StrategyDraft`
3. 人工审核草案
4. 创建 `Strategy` / `StrategyVersion`
5. 生成 `StrategyCodeArtifact`
6. 相关性过滤后形成 `SignalEnsemble`（多策略/alpha 信号融合）
7. 生成 `MetaLabel`（二级仓位判定）
8. 启动 `BacktestRun`（基于融合后的候选交易评估）
9. 可选启动 `OptimizationRun`
10. 计算准入结论
11. 启动 `PaperRun`
12. Decision Veto Agent 复核信号（否决为 true 则终止本次执行）
13. 监控 `RiskEvent`
14. 生成 `ReviewReport`
15. 沉淀 `FailureRecord`
16. 更新策略状态

### 4.2 外部研究任务链

1. 创建 `IngestionJob`
2. 抓取研究源
3. 标准化研究条目
4. 交给 `Research Agent`
5. 生成 `StrategyIdea`

### 4.3 风险事件任务链

1. 采集事件
2. 标准化分类
3. 生成 `RiskEvent`
4. 交给 `Risk Agent` 或 Risk Engine
5. 更新运行约束

---

## 05 编排约束

- 未审核的 `StrategyDraft` 不得升级为正式策略
- 未通过 Validation 的策略不得进入执行链
- 未经过 Risk Engine 的信号不得触发执行
- 未经相关性过滤形成 `SignalEnsemble` 与 `MetaLabel` 判定的信号不得进入 `BacktestRun`/执行链
- 否决为 `true` 的信号不得进入 `ExecutionSignal`
- 未被 Review 回写的失败不得视为闭环完成

---

## 06 人工与自动边界

自动化负责：

- 结构化转换
- 回测执行
- 优化执行
- 风险事件分类
- 日报生成

人工负责：

- 草案确认
- 模拟盘准入
- 实盘准入
- 熔断恢复
- 策略淘汰/升级决策

---

## 07 调度实现建议

- FastAPI：提交任务与查询状态
- Celery：执行 AgentTask
- Redis：短期队列与状态协调
- PostgreSQL：事实与最终状态落库

---

## 08 下一步承接

本设计包完成后，应继续进入：

- 执行/风控/复盘设计包
- 然后再将任务链落为 Celery 任务图和 API 操作面
