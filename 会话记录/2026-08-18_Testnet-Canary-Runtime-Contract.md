# Testnet Canary Runtime Contract（2026-08-18）

## 结果

- Binance Testnet Canary 执行范围固定为 BTC/USDT、ETH/USDT、SOL/USDT、XRP/USDT、BNB/USDT。
- 运行时合同统一为：30x、目标/最大保证金 5%、单币名义 1.50x Equity、最多 5 仓、总名义 7.50x Equity、risk_per_trade 0.10。
- `TESTNET_CANARY` + `TESTNET_SAMPLING` 下 E-003、volatility shock、R2、E-004 风险只记录 diagnostic；运行状态、交易所、数据 freshness、对账和人工 kill switch 仍 blocking。
- 新 sizing 会把交易所现有仓位的 mark-price 名义计入 7.50x aggregate ceiling；现有仓位不被配置切换修改。
- 公共 `AutoTradingSettings` API schema、bootstrap、operator profile、scheduler、apply 脚本和前端显示已同步；API 可接受 `max_total_exposure=7.50`。
- 发布工具新增 `一键推送.cmd` / `scripts/git_publish.ps1`，验证 branch、origin、worktree、远端 ahead、认证恢复、大文件边界和 local SHA == remote SHA。

## 验证

- `ruff check .`：`All checks passed!`
- 核心 Python mypy：`Success: no issues found in 9 source files`
- 目标合同/调度测试：`91 passed`
- 前端：`21` test files / `113 passed`
- 全仓 pytest：`1694 passed, 16 skipped, 1 failed`
- 唯一失败：`tests/services/test_daily_review.py::test_daily_review_keeps_all_terminal_reasons_for_same_symbol`；单独重跑仍失败，未发现与本轮 sizing/runtime 改动的因果关系，保持原业务未动。
- PowerShell 发布脚本解析通过；本轮未执行真实 GitHub push，也未伪造远端 SHA。
- 本轮未执行真实 Binance 下单；Testnet 真实证据沿用既有运行记录，未将代码测试当作成交证明。
