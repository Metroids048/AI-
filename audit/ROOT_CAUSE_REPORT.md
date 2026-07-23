# Root Cause Report

## 1. TradeIntent 执行契约断裂（高证据，局部回归）

代码证据：`9745089` 在 `gateway.py` 引入 TradeIntent normalizer，并在 `:340-341` 对缺失 `market_rules_snapshot` 抛异常；`paper_cycle_orchestrator.py:1296` 仅在 active config 下附加 intent，`paper_exchange_execution.py:676` 调用 `gateway.submit_order()` 前没有补齐 snapshot。运行证据：过去 24 小时主 lane 17 次完全相同的 `market_rules_snapshot is required for TradeIntent execution`。解释力：周期、策略和 gatekeeper 都可“成功”，代码也进入了 `gateway.submit_order()`，但在 CCXT/交易所 `client.create_order()` 前 fail-closed。最小验证：在 paper adapter mock 中给同一 intent 提供固定 market snapshot，证明 normalize -> submit -> ack；失败条件：仍在 gateway 前抛异常则推翻。分类：局部契约/回归。

## 2. Lease 丢失后旧 scheduler task 未 fencing（中等证据，架构问题）

代码证据：`scheduler.py:348-360/375-405` 用 `asyncio.shield(run_task)` 等待，lease renewal 失败只写 error，不取消或拒绝 runner；账本 103 cycles 中 102 completed、1 claimed，存在一个从 09:59:50 延续至次日 01:29:59 的异常长 cycle。解释力：代码允许 lease 丢失后的旧任务继续写 decision/order/reconcile，重复 slot 为 0 仍不代表时间线正确。但当前账本没有 lease-loss 字段，本轮也没有保存能把这次长 cycle 与实际 lease-loss 唯一关联的日志，因此它是架构缺陷与时间线污染的中等证据假设，不是已证实的该 cycle 因果。最小验证：两个 mock scheduler 让 owner lease 过期，断言旧 task 不得写 cycle；失败条件：若已有 fencing 但账本仍污染，则转向 persistence 时间源。分类：架构问题。

## 3. 外部 ETH 仓位恢复与保护价绑定错误（高证据，架构问题；交易所因果仍部分未证实）

代码证据：`paper_exchange_execution.py:327+` 将未跟踪 exchange position 恢复进自动 run；`paper_cycle_orchestrator.py:1452+` 仅按 run_id + symbol 找 latest filled entry/protection，未校验方向、origin、exchange position identity。运行证据：ETH short 恢复、`reconcile_close_unprotected_position`、旧保护价 `1872.22425`；交易所只读 order `14240828026` 为 `ETHUSDT BUY MARKET reduceOnly`，在北京时间 09:29:28.611 成交 15.144 @ 1933.59000；本地两次退出记录为 `exchange_already_flat` 且挂旧 cycle。旧保护价低于人工 short entry 1944，却被 short stop 的 `bar.high >= stop_price` 判断使用，因此会立即满足，而不是方向正确的 short stop。解释力：可解释“手动空单被自动平掉”，但不能仅凭本地账本确定哪一次代码调用发起了交易所订单。最小验证：只读关联 exchange order id/client id 与本地 order timeline，并在 shadow fixture 中模拟外部 short；失败条件：若 identity 明确属于人工 close endpoint，则推翻自动归因。分类：架构问题；当前结论是“不是正常卖点设计，因果链待交易所回执/本地时间线补证”。

## 唯一下一步最小实验

在 Testnet/paper mock 中执行 A 通道的**单个 BTC/ETH synthetic intent**，固定 market snapshot 和现有仓位/杠杆配置，只验证 `TradeIntent -> risk -> normalizer -> adapter mock -> ack -> reconcile`；禁止实时策略、禁止下单 POST、禁止修改阈值。
