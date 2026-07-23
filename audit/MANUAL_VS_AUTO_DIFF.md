# Manual vs Automatic Diff

| 维度 | 手动 | 自动 Paper |
|---|---|---|
| API 起点 | `apps/api/routers/runs.py:658` `/manual-orders`、`:678` `/close-position` | scheduler -> task -> runtime -> orchestrator |
| 服务 | `services/execution/manual.py:40/55` | `PaperCycleOrchestrator` + `PaperExchangeExecutionService` |
| intent | 手动 request 直接构造 ExecutionOrderRequest；通常没有 TradeIntent normalizer 要求 | `_with_open_trade_intent()` 在 active config 下附加 TradeIntent |
| gateway | manual service 可直接调用 `gateway.submit_order` | 自动 mirror 先经过 paper order/lifecycle，再调用 gateway |
| 仓位状态 | manual service `_open_or_replace_position` 写本地 snapshot | 自动先 reconcile exchange truth，再按 run/symbol 绑定保护价 |

自动与手动在 router/service 函数处分叉，不共享同一订单上下文。用户手动开单本身证明交易所账户可开仓，但不证明自动 request 携带完整 market rules、lease fencing 或 reconciliation identity。
