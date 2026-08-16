# 2026-08-16 Exit Order Fill Lineage 与 Natural Exit 观察

## 完成的可执行工作

- 新增只读 `scripts/build_exit_order_fill_lineage.py`，将 30 笔 R0-R3 episode
  与 V2 intent、entry order/fill、protection event、reduce-only exit fill、incident
  串为 canonical lineage。
- 对所有 exit fill 强制校验 `exchange_order_id` 等于对应
  `ProtectionTriggered.event_payload.exchange_order_id`；缺失 intent、trigger event 或
  ID 不匹配均保留为单笔 audit failure，而不终止整份报告。
- 产物 `docs/audits/2026-08-16-exit-order-fill-lineage.{json,md}`：30/30 已验证订单
  关联，15 STOP / 15 TARGET，零异常 exit，零 quantity mismatch partial fill。
- 当前受管 BTC/USDT long 的 P1 replacement 已有真实 receipt：stop 从 `62744.5`
  调整为 `62976.0229`（Binance `1000000168673444`），TP `1000000167954361` 仍 live。

## 验证

- `ruff check scripts/build_exit_order_fill_lineage.py tests/scripts/test_build_exit_order_fill_lineage.py` -> All checks passed.
- `mypy scripts/build_exit_order_fill_lineage.py` -> Success: no issues found in 1 source file.
- `pytest -q tests/scripts/test_build_exit_order_fill_lineage.py tests/services/test_risk_controls.py tests/services/test_automated_trading_protection.py` -> 32 passed.
- 全仓 `ruff check .` 被既有 `scripts/verify_gate17_e2e.py:77` 的 C416 阻断；全仓
  `mypy .` 被 root/archive `check_positions` 重名阻断；全仓 `pytest -q` 超过十分钟无
  输出后中断。以上均未触及本次文件。

## 恢复点

Scheduler 继续 ACTIVE / BINANCE_TESTNET。2026-08-16 05:52:01Z、05:53:01Z、05:54:01Z
三次采样均为单一 BTC/USDT `0.2764` long、两张 live reduce-only 保护单、HEALTHY
reconciliation；尚无自然 reduce-only exit fill。下一步只需继续观察自然止损或止盈，
随后以 Binance exit order/fill、本地与交易所 flat、HEALTHY reconciliation 完成最终闭环。
