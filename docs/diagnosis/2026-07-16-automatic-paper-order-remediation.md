# 自动 Paper 开单整改记录（2026-07-16）

## 目标与边界

目标是在不削弱止损、确定性风控、数据新鲜度或实盘保护的前提下，恢复可审计的自动 Paper 订单样本。当前风险参数使用已确认的偏激进 Paper/Testnet 基线；不适用于实盘。

## 本地证据

- 本地 SQLite 快照不是“LLM 全拒”：存在 `bet_taken`、`meta_label_bet_skipped`、`multi_timeframe_disagreement` 等多种状态；已持久化的订单拒绝主要是 `net_edge_after_cost_negative`。
- LLM 供应商不可用会被记录为 advisory；执行拒绝仍只由高风险事件、数据/仓位/止损等确定性条件触发。
- 本地一键启动使用 `RuntimeScheduler`，原先缺少 Celery 已有的信号边际统计周期刷新。
- 调度器曾错误扫描所有 `paper_status=running` 的运行，包括人工 sandbox，污染了拒单统计。
- “signal_observation” 配置声明跳过成本门槛，但订单追踪没有携带该通道标签，通用 Gatekeeper 因而仍执行成本拒绝。

## 已实施

1. 自动调度只处理 `execution_profile.auto_schedule_enabled=true` 的运行；人工 sandbox、链路验证、未验证 Carry 和 Swing 不会进入 7x24 循环。
2. 自动创建的观察通道固定为 `paper_only` 且禁止 gateway 镜像；其订单标记为 `strategy_performance_eligible=false`。
3. 观察通道身份会传入订单追踪，因此仅该通道跳过 `net_edge_after_cost`、多周期确认和 MetaLabel 采样筛选；它仍要求真实技术信号与有效 ensemble，且止损、风险事件、行情新鲜度、杠杆、仓位、回撤和亏损限制仍然生效。
4. 本地调度器增加每周 `refresh_signal_edge_stats`，首次运行延后一个周期，避免一键启动时执行长时间回放；统计任务异常不会停止自动交易循环。
5. 偏激进 Paper 基线由回归测试锁定：单笔风险 5%、单币敞口 35%、总敞口 90%、40x 杠杆、组合初始风险 25%、最多 10 个仓位。

## 当前自动运行集合

| 通道 | 自动调度 | 执行目标 | 成本门槛 | 业绩验证 |
| --- | --- | --- | --- | --- |
| 主技术方向策略 | 是 | 本地 Paper | 保留 | 可以进入后续验证 |
| 信号观察通道 | 是 | 仅本地 Paper | 跳过成本、MTF、MetaLabel 采样筛选 | 不可计入 |
| 人工 sandbox | 否 | 人工触发 | 原规则 | 不适用 |
| 链路验证 | 否 | 人工触发、仅 Paper | 跳过 | 不可计入 |
| Carry / Swing | 否 | 研究候选 | 原规则 | 尚未完成验证 |

## 上线验收

完成代码更新后必须通过标准启动器重启本地 Paper 运行时，使 bootstrap 写入数据库配置。验收时确认：

1. 调度状态显示新周期、市场心跳和 Top10 覆盖正常。
2. 自动运行集合只包含主技术方向策略与信号观察通道。
3. 观察订单存在 `observation_only_mode=true`、`strategy_performance_eligible=false` 和 `mirror_to_gateway=false`。
4. 主技术策略仍可看到真实成本门槛的拒绝原因；观察通道不出现 `net_edge_after_cost_negative`。
5. 任一通道都不接受无止损、数据陈旧、风险事件阻断或超出风险上限的订单。
