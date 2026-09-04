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
| RUNTIME | PASS | 官方 `一键启动.cmd` 已在最终 executable SHA `eb44e6f` 上启动；Scheduler/Supervisor/Worker 在线，V2 cycle 完成 |
| ADJUDICATION | PASS | 双 SQLite 测试数据库、正式 `account_scope_key`、`0025 -> head` migration tests 通过 |
| REAL_RECOVERY | PASS | launcher 向 supervisor 传播非 secret account scope；两次真实 worker replacement 后均恢复 `TRADING_READY` 与 `HEALTHY` |
| REOPEN | PASS | 未发现新的本地 P0/P1；旧 V2 contract drift 已由正式 rebaseline 处理 |
| NATURAL_L2 | ARMED_AND_WAITING_FOR_NATURAL_SIGNAL | 未制造信号；没有自然 closed-bar candidate，因此未执行真实 Testnet entry/exit |
| L3 | NOT_REQUIRED | 本轮最终目标到 `TRADING_READY`；不扩展至自然信号之后的生命周期验收 |
| L4 | NOT_REQUIRED | 本轮最终目标到 `TRADING_READY`；不扩展至自然信号之后的生命周期验收 |
| CORE_EXECUTION_FREEZE | ACTIVE | unified 与 V2 frozen contract 已 refreeze 到最终 executable SHA，且包含 account-writer |

## Runtime现场

本节的旧 prekill 观察已由后续官方 launcher 重启与两次受控 worker recovery 覆盖。没有停止 supervisor、没有手动清除 recovery hold、没有探测或输出账户凭据。

## Verification

- `pytest -q` -> `2063 passed, 7 skipped, 17 warnings`
- `ruff check .` -> `All checks passed!`
- `mypy` -> `Success: no issues found in 315 source files`
- `python scripts/verify_automated_trading_contract.py --verify-baseline --verify-head` -> current HEAD protected paths match automated trading baseline
- `python scripts/verify_scheduler_multi_instance_24h.py` -> `claimed_slots=96`, `duplicate_winner_slots=0`, `passed=true`
- `git diff --check` -> clean

明确边界：`NO LIVE TRADING PERFORMED`。本记录不把本地测试、mock、历史订单号或本地数据库状态当作真实 Binance 账户证据。

## Runtime Recovery Finalization

原可执行 SHA `82f33fd108e2b96c5236ace012a5eb55388563c2` 已由下述 dead-writer recovery 修复取代。

- 官方 `一键启动.cmd` 启动后的主运行态为 `ACTIVE / BINANCE_TESTNET / TESTNET_CANARY`；`Scheduler ONLINE`、Supervisor PID `27816`、worker 正常心跳，Active ConfigSnapshot 为 `baceeb5f-848e-453d-85fa-f4bb11d06443`，Pending=None。
- `ETH_ATTRIBUTION_001` 的只读 immutable-evidence preflight 返回 `exchange_writes=0`；两个受影响位置已由正式两阶段 adjudication 投影为 `CLOSED`，不构造、不提交、不撤销任何交易所订单。
- Canonical account writer 为 `BINANCE:TESTNET:primary_testnet`，绑定数据库 identity 与当前运行数据库一致；alternate database 维持非 writer 边界。
- Worker Recovery #1：`2772 -> 1152`；Worker Recovery #2：`1152 -> 26548`。两次均由同一 Supervisor 接管，在新的健康 V2 cycle 后自动清除 fail-closed hold，得到 `entry_authorized=true`、`TRADING_READY`、`reconciliation=HEALTHY`。
- Unified frozen contract 已 refreeze 到当时的 executable SHA；后续 account-writer recovery 已按新的 executable baseline 再次 refreeze。
- `NATURAL_L2=ARMED_AND_WAITING_FOR_NATURAL_SIGNAL`。没有人为制造候选、订单或成交；Production/Mainnet 未授权，`NO LIVE TRADING PERFORMED`。

## Dead Writer Lease Recovery

最终可执行 SHA：`eb44e6f8cda6442487d25e6441e2af8508e52357`。

- 真实重启复现了 `ACCOUNT_WRITER_ALREADY_HELD`：launcher 强制停止旧 supervisor 时，旧 owner 的 360 秒 lease 未能执行 Python `finally` 释放。
- 修复仅允许严格 `local-host:pid` owner 且 OS 确认 PID 已死时，针对同一个已绑定数据库接管并递增 generation；远端、未知、带附加字段或仍存活 owner 保持 fail-closed。
- 回归：AWF `27 passed`、launcher/account-writer focused `67 passed`、isolated full pytest `2057 passed, 16 skipped, 2 warnings`、Ruff/mypy PASS。
- 修复后的官方 `一键启动.cmd` 启动到 Supervisor `26864`、Worker `21036`、有效 Active Snapshot、`TESTNET_CANARY`、`TRADING_READY`、writer `VALID` 和 `reconciliation=HEALTHY`。
- 两个冻结合同都纳入 `account_writer.py` 并 baseline 到最终 executable SHA。`NATURAL_L2=ARMED_AND_WAITING_FOR_NATURAL_SIGNAL`；`NO LIVE TRADING PERFORMED`。
