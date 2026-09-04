# Final Infrastructure Closeout

日期：2026-09-04

候选提交：`f05e789ef5c9f73759c01efe6b6d7795f994864d`

冻结契约 rebaseline：`5994139`，当前冻结基线为 `f05e789`。

## 实现

- 增加 machine-global Binance Testnet account writer registry，按 account scope、SQLite database identity、owner、generation 和 lease fencing。
- Entry、leverage、protection、stop replacement、reduce-only exit、emergency close、cancel 和 recovery mutation 统一经过 account-writer critical section。
- Direct Supervisor 复用 `scripts/prepare_database.py` 的 Alembic preparation。
- Adjudication manifest 使用正式 `account_scope_key`，Exit/Recovery 的 writer 校验 fail closed。
- 修复非 `AccountWriterCapability` 伪对象被当成 registry 路径的问题；未生成新的 `MagicMock/` 副产物。
- 未修改策略、R2、风险参数、SL/TP、Canary、Production Gate、前端或 legacy frozen pipeline。

## Acceptance Ledger

| 项目 | 状态 | 证据/边界 |
|---|---|---|
| STATIC | PASS | `ruff check .`、`mypy`、全仓 pytest 通过 |
| ACCOUNT_WRITER | PASS | AWF contract 通过；跨 DB、generation、crash takeover、binding/rebind、in-flight 序列化均有测试 |
| MUTATION_FENCING | PASS | adapter/gateway/application focused tests 通过；无 capability 不调用 Binance 的回归通过 |
| RUNTIME | PARTIAL | 加速 24h 双实例验证 `96/96`、重复获胜 `0`；真实 launcher 未按本轮代码重启 |
| ADJUDICATION | PASS | 双 SQLite 测试数据库、正式 `account_scope_key`、`0025 -> head` migration tests 通过 |
| REAL_RECOVERY | BLOCKED | 未配置 `BINANCE_ACCOUNT_SCOPE_ID` / `BINANCE_OPERATOR_IDENTITY`，未探测账户端点，未读取凭据 |
| REOPEN | PASS | 未发现新的本地 P0/P1；旧 V2 contract drift 已由正式 rebaseline 处理 |
| NATURAL_L2 | BLOCKED_NO_NATURAL_SIGNAL | 未制造信号；未执行真实 Testnet entry/exit |
| L3 | NOT_RUN | 依赖真实恢复和账户绑定前置条件 |
| L4 | NOT_RUN | 依赖真实恢复和 canonical DB binding 前置条件 |
| CORE_EXECUTION_FREEZE | NOT_ENTERED | 仅在真实账户绑定、ETH recovery、canonical DB 和稳定观察期证据齐全后进入 |

## Runtime现场

检测到一键启动相关 API 与 frontend 进程。`i1_prekill_guard_readonly.py` 返回 `FAIL`，原因是当前运行控制行和受保护 BTC/USDT 位置不满足该脚本前置条件，因此按安全规则没有停止现有 Scheduler，也没有在未核对账户状态时重启。

## Verification

- `pytest -q` -> `2063 passed, 7 skipped, 17 warnings`
- `ruff check .` -> `All checks passed!`
- `mypy` -> `Success: no issues found in 315 source files`
- `python scripts/verify_automated_trading_contract.py --verify-baseline --verify-head` -> current HEAD protected paths match automated trading baseline
- `python scripts/verify_scheduler_multi_instance_24h.py` -> `claimed_slots=96`, `duplicate_winner_slots=0`, `passed=true`
- `git diff --check` -> clean

明确边界：`NO LIVE TRADING PERFORMED`。本记录不把本地测试、mock、历史订单号或本地数据库状态当作真实 Binance 账户证据。
