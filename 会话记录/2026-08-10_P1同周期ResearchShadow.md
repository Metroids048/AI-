# 2026-08-10 P1 同周期 Research Shadow 会话记录

## 状态

`IMPLEMENTATION_COMPLETE_PENDING_REVIEW`

P1 只解决一个根因：研究候选脱离真实 ACTIVE V2 同周期观测链。没有开始
P2 晋级、优化、参数调优或策略切换。

## 实现

- ACTIVE path 与 research path 已按当前代码证明并写入计划
  `docs/superpowers/plans/2026-08-10-p1-same-cycle-research-shadow.md`。
- 修改 `services/execution/v2_scheduler_entry.py`：同一 ACTIVE symbol cycle
  的 closed 15m `TimeframeView` 作为 Research context 的 15m 输入；所有
  ACTIVE symbols 完成并释放 writer/slot lease 后才执行 evidence append。
- 修改 `services/strategy_library/proposal_pipeline.py`：固定三个研究版本，
  单候选异常隔离为结构化 `SHADOW_STRATEGY_ERROR`。
- 新增只读 `scripts/verify_p1_same_cycle_research_shadow.py`：SQLite
  `mode=ro`、同周期 envelope/lane/identity/reference 校验和 RESEARCH
  lineage mutation ledger。

## Runtime Evidence

- 入口：`一键启动.cmd`，两次均退出码 0；最终 cutover UTC
  `2026-08-10T13:15:03.869648Z`。
- Final runtime: `ACTIVE`, `BINANCE_TESTNET`, `testnet_sampling_v2`,
  `entry_authorized=true`, `legacy_writer=false`, external baseline captured,
  registered job only `automated_trading_v2_cycle`.
- Change effect: before `189`, after `192`, same-cycle matched `192`, unmatched
  `0`。BTC 与 ETH 的例子均为 bar `2026-08-10T13:15:00Z`；BTC ACTIVE
  terminal `POSITION_ALREADY_OPEN`，三 Shadow 均 `SHADOW_NO_SIGNAL`。
- Shadow mutation ledger: intents `0`, exchange orders `0`, positions created
  `0`, positions modified `0`, protection created `0`, protection modified `0`。
- Binance/local before: 2 exchange positions / 2 local managed positions and
  4 open protection orders. After natural runtime: 1 / 1 and 2 BTC protection
  orders remain; the ETH protection exit was an existing ACTIVE protection
  chain change, not a research lineage mutation. Reconciliation stayed
  `HEALTHY`.

## Verification

- Red tests observed before implementation for missing verifier, per-symbol
  observer ordering, tampered same-cycle envelope, and research lineage escape.
- P1 focused: `30 passed`.
- P0/U1/reconciliation: `79 passed, 1 skipped`.
- Full pytest: `1436 passed, 7 skipped, 2 failed`; failures are the known
  candidate registry assertions expecting 9 while registry has 10.
- Touched Ruff: `All checks passed!`; mypy: `Success: no issues found in 225
  source files`.
- Full Ruff has three pre-existing unrelated script findings (two B023 and one
  C416). `pre-commit run --all-files` otherwise passed its format/config/mypy,
  targeted suites, and skill-copy checks, but exits 1 on those baseline gates.

## Scope And Follow-up

No launcher, scheduler state, strategy rules, risk values, exchange credentials,
reconciliation rules, database migration, second writer, or P2 behavior changed.
Independent read-only review found and drove two verifier hardening checks:
top-level ACTIVE strategy identity and cycle `execution_mode` must be
`BINANCE_TESTNET`; both now have regression coverage. Final status remains
`IMPLEMENTATION_COMPLETE_PENDING_REVIEW`.
The documented central sync script path does not exist on this machine, so
central synchronization remains unavailable and is not fabricated as complete.
