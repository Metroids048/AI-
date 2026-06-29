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
- 重大宏观事件暂停
- 稳定币异常紧急处理
- API 连续失败停机
- 数据中断禁开仓

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

