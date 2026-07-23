# Automatic Exit Call Graph

```text
PaperCycleOrchestrator.run_cycle()
  -> PaperExchangeExecutionService.reconcile_local_positions_with_exchange()
     -> gateway.reconcile()（只读账户/仓位查询）
     -> 未绑定 active_positions 的 exchange position 恢复为 Paper position
     -> _resolve_protective_levels()
        -> find_latest_filled_entry_order(run_id + symbol)
     -> _check_protective_trigger()
     -> _close_order_request(close_only/reduce_only, market)
     -> gatekeeper.submit_order()
     -> paper close_position()
     -> gateway.submit_order()（若 mirror armed）
```

`paper_exchange_execution.py:327+` 将交易所仓位按 symbol 恢复到自动 Paper run；`paper_cycle_orchestrator.py:1452+` 以同一 `run_id + symbol` 找最近 filled entry order，未证明方向、来源或 exchange position identity。账本显示 ETH `reconcile_close_unprotected_position` 与旧保护价 `1872.22425` 的两次本地 `exchange_already_flat` 记录。

交易所只读订单核验：order `14240828026`，ETHUSDT `BUY MARKET reduceOnly`、FILLED、数量 `15.144`、均价 `1933.59000`、北京时间 `2026-07-23 09:29:28.611`。这与用户看到的成交一致；本地 snapshot 却挂在旧 cycle 时间，说明本地 cycle/状态时间线污染，不能把该价位称为正常策略卖点。旧价 `1872.22425` 被当作 short 的 stop 条件时位于人工 entry `1944` 下方，按 `bar.high >= stop_price` 会立即满足，这不是一个方向正确的 short stop 设计。
