# 执行 / 风控 / 复盘设计包

## 文档定位

本文件定义 Execution Layer、Risk Engine、Review Layer 的运行模型、边界与状态约束。

---

## 01 Execution Layer

### 1.1 职责

- 接收通过验证的执行信号
- 路由订单
- 记录执行结果
- 与 Risk Engine 协同控制执行

### 1.2 核心对象

- `ExecutionSignal`
- `OrderExecution`
- `PositionSnapshot`

### 1.3 执行前置检查

- 信号必须来自通过验证的策略版本
- 信号必须已经过 `SignalEnsemble` 融合与 `MetaLabel` 二级仓位判定，且 `bet_decision = bet_taken`
- 信号必须未被 LLM 否决（`veto = false`，见 03a）
- 必须具备止损计划
- 必须通过当前风险开关
- 必须通过交易所与数据可用性检查

### 1.4 硬性拒绝规则

- 无止损拒绝
- 超出单品种敞口拒绝
- 超出总仓位拒绝
- 熔断期拒绝
- 数据中断期间拒绝新开仓

---

## 02 Risk Engine

### 2.1 风控主目标

- 防止可避免的大亏损
- 把风险控制从“人工习惯”变成“代码级约束”

### 2.2 风控规则族

- 单笔风险限制
- 单品种最大仓位
- 总持仓上限
- 最大同时持仓
- 最大杠杆
- 当日最大亏损
- 单周最大亏损
- 连续亏损保护
- 最大回撤保护
- 极端回撤熔断
- 重大宏观/新闻事件暂停（分级触发，见 2.2a）
- 稳定币异常紧急处理
- API 连续失败停机
- 数据中断禁开仓

### 2.2a 新闻/消息面事件分级触发规则

新闻与社媒数据的用法是“过滤器/否决器”，不是“新闻驱动开单”的信号源。具体分级：

- 高严重度（`RiskEvent.severity = high`，如重大监管/交易所暴雷/黑天鹅事件）：
  - 暂停新开仓，暂停时长为可配置参数（默认建议区间几分钟到几十分钟，具体数值留给 Risk Engine 实现时配置，不在设计层硬编码）
  - 已有持仓的止损按可配置比例上移（多头）/下移（空头），收紧风险敞口
- 中严重度：仅记录 `RiskEvent`，不触发暂停，供 Review Layer 事后分析
- 低严重度：仅落库，不产生任何执行层动作

触发与解除都必须落一条 `RiskEvent` 记录，`resolution_status` 走 `detected -> acknowledged -> mitigated -> resolved`，不允许静默恢复。

### 2.3 风控结果

风控结果应统一输出为：

- `allow`
- `deny`
- `reduce_exposure`
- `pause_strategy`
- `pause_account`
- `hard_stop`

---

## 03 RiskEvent 模型作用

`RiskEvent` 是风险系统统一输入对象。

按来源分类：

- 宏观事件
- 新闻事件
- 社媒事件
- 市场结构风险
- 执行异常
- API/交易所异常
- 数据缺口
- 账户/策略风控越限

---

## 03a LLM 否决职责边界

### 3a.1 定位

- LLM（Decision Veto Agent，见 `agent-and-orchestration-design.md`）只在信号融合与二级仓位判定完成之后、`ExecutionSignal` 生成之前介入
- 输入：`SignalEnsemble`、`MetaLabel`、近期 `RiskEvent`（新闻/社媒异常）
- 输出：`veto: bool` + `veto_reason`（自然语言，供 Review Layer 复盘引用）

### 3a.2 硬性边界

- LLM 不得输出方向、仓位大小、入场/止损/止盈价格——这些必须已经由 `SignalEnsemble`/`MetaLabel` 产出
- LLM 只能做“否/不否”的二元判断，不能修改已有信号内容
- 否决结果本身作为 `AgentTask` 输出对象记录，不直接写入执行层状态；Execution Layer 的前置检查（见 1.3）读取该否决结果作为准入条件之一
- 否决为 `true` 的信号不得进入 `ExecutionSignal`（与 `agent-and-orchestration-design.md` 05 编排约束一致）
- LLM 的复盘生成职责（辨识 alpha decay 等）由 Review Layer 承接，与否决职责分离但共享同一批输入

---

## 04 Review Layer

### 4.1 复盘主目标

- 每日识别哪些策略表现异常
- 分析回测预期与现实偏差
- 形成继续/暂停/淘汰建议
- 把经验回写到策略库

### 4.2 ReviewReport 应回答的问题

- 今天哪些策略执行了
- 哪些策略最差
- 偏差来自市场、参数、执行还是风险
- 哪些策略应暂停
- 哪些策略应调参
- 哪些策略应淘汰

### 4.3 FailureRecord 的价值

- 为 Strategy Layer 提供失败知识
- 为 Research Agent 提供反向学习材料
- 为 Review Layer 提供模式聚类基础

---

## 05 执行 / 风控 / 复盘联动

主联动链路：

`ExecutionSignal -> OrderExecution -> PositionSnapshot -> RiskEvent -> ReviewReport -> FailureRecord -> Strategy`

约束：

- 执行事实必须可供 Review 读取
- RiskEvent 必须进入 Review 分析
- Review 结论必须回写 Strategy 状态或失败记录

---

## 06 P0 实现边界

P0 必做：

- Execution Layer 基本对象与前置检查
- Risk Engine 规则框架
- ReviewReport 与 FailureRecord 的沉淀路径

P0 只定框架：

- 更复杂的跨账户风控联动
- 更复杂的多市场风险联动
- 更复杂的事件聚类与根因分析
