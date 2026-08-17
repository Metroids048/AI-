# 执行范围扩容：2 -> 5（2026-08-16）

## 结果

- 执行范围：BTC/USDT、ETH/USDT、SOL/USDT、XRP/USDT、BNB/USDT。
- execution scope hash：`9d4e56ae53f9d0b1047efebc5ac48b28fa0cfa72d71b28178dc47dd9b11d124d`。
- SOL/XRP/BNB：各 61 根当前 1h 数据，无异常 gap；Binance USDT-M Testnet 合约均为 trading，精度和 min-notional 已解析。
- Shadow：10/10 轮健康，零提交、零异常。
- 精确范围验收：5 个 symbol、10 个真实 Testnet 成交订单；ETH 空仓和原有两张保护单 ID 保留；验收记录 `399de803-e4b0-40f8-9b9b-de502347e669`；V2 已重新军备。
- 运行时：五币 heartbeat、五币自然 V2 cycle、ACTIVE startup contract 均通过；blocker check 为 `execution_ready=True`。

## 基线说明

初始 09:53 UTC 快照包含 BTC long `0.2764` 及两张 BTC 保护单；在代码改动完成、验收前的只读快照中 BTC 已消失，ETH 仍在。该漂移先于验收，未由扩容代码触发，也未尝试用新订单伪造原 order ID。验收实际保留的是 10:05 UTC 的 ETH-only baseline。

## 验证

- `pytest -q`：1635 passed, 7 skipped。
- `mypy`：257 source files，无错误。
- `ruff check .`：仅已知 `scripts/verify_gate17_e2e.py:77` C416。
