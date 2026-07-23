# Automatic Entry Call Graph

```text
一键启动.cmd
  -> launch-paper-console.ps1
    -> run-local-paper-scheduler.py
      -> RuntimeScheduler.start()
        -> _run_coordinated_once()
          -> tasks.run_all_paper_runtime_cycles()
            -> run_paper_runtime_cycle.run()
              -> PaperRuntimeService.run_cycle()
                -> PaperCycleOrchestrator.run_cycle()
                  -> DecisionPipeline.evaluate()
                    -> strategy / MTF / ensemble / meta-label / LLM
                  -> PaperSignalGenerator.generate_order()
                  -> _with_open_trade_intent()
                  -> ExecutionGatekeeperService.submit_order()
                  -> PaperExchangeExecutionService.ensure_binance_execution()
                    -> BinanceUsdtPerpetualGateway.submit_order()
```

关键分叉：`paper_cycle_orchestrator.py:1296` 只有 active `ConfigSnapshot` 才把 `TradeIntent` 附到 order request；`paper_exchange_execution.py:676` 调 gateway 时，request 仍没有 `market_rules_snapshot`，于是 `gateway.py:340-341` 在构造实际 exchange order 前抛出 `ValueError`。账本 24 小时主 directional lane 17 次均为 `binance_auto_execute_failed: market_rules_snapshot is required for TradeIntent execution`，因此未调用 CCXT/交易所 create-order 方法。

Paper 本地订单可能先写 `OrderExecution`，再由 gateway mirror 更新为 `gateway_failed`；“cycle completed”不等于 order acknowledged。
