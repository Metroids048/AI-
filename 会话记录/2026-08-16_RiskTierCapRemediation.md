# Risk Tier Remediation

日期：2026-08-16

## 目标

纠正上一轮把 `5x / 0.05` 误当成目标的问题，统一恢复为 `50x + 5% 保证金`：
`max_leverage=50`、`max_margin_fraction=0.05`、`max_symbol_exposure=2.50`，并保留
`risk_per_trade=0.01`。不修改既有 ETH/XRP 仓位、杠杆或保护单。

## 结果

- 默认、动态波动率、legacy fallback、slider scaling、V2 operator profile、API
  auto-settings、PaperSignal fallback、bootstrap 和前端自动开单设置均已同步。
- 活跃 directional run `35298c65-cdbe-4bee-bee3-b7ded07c3204` 已激活
  ConfigSnapshot `ff2ebdc1-ee1d-4ec3-a137-167112cb36a7`。
- 五个 symbol 的真实解析均为 `50x / 2.50` 名义敞口，保证金比例为 `0.05`，
  `risk_per_trade=0.01`。
- 交易所当前仓位仍为 ETH short `9.266 @ 50x`、XRP short `972 @ 20x`，四张
  reduce-only 保护单均为 NEW/active；对账 `ok`，local/exchange 均为 2 个仓位。
  以 2026-08-16 20:25:05 的账户权益 `6938.77033887` 计算，ETH notional fraction
  `2.510569`（略超出新的 2.50 名义敞口上限），XRP `0.140096`（未超出）。

## 验证

- 定向 ruff：通过。
- 定向 mypy：无错误。
- 前端 Vitest：`112 passed`；前端 build：通过。
- 全仓 pytest：`1638 passed, 7 skipped`。
- 全仓 mypy：`Success: no issues found in 257 source files`。
- 全仓 ruff：仅保留已知基线 `scripts/verify_gate17_e2e.py:77 C416`。

## 最终运行时证据

`scripts/verify_live_sizing_profile.py` 读取当前 active snapshot
`ff2ebdc1-ee1d-4ec3-a137-167112cb36a7`：

- profile-wide：`risk_per_trade=0.01`、`max_leverage=50`、
  `max_margin_fraction=0.05`、`max_symbol_exposure=2.50`、
  `max_total_exposure=5.00`。
- BTC/ETH/SOL/XRP/BNB 五个 symbol 的 cycle resolution 完全一致：
  `50x / 0.05 margin / 2.50 exposure / 0.01 risk_per_trade`。
- 现有 ETH/XRP 仓位没有调用交易所 `setLeverage`、平仓或保护单变更路径。
