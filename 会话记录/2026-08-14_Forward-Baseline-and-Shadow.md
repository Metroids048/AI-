# Forward Baseline 与 Shadow 证据基础设施

## 范围

严格按锁定顺序处理：不改策略规则、不改风控阈值、不改 Binance 执行链，只建立未来决策的不可变输入证据、确定性重放和 Shadow 记录。

## 已落地

- 新增 `v2_decision_snapshots`、`v2_shadow_records`、`v2_shadow_outcomes`，迁移 `0021`。
- 每个 V2 cycle 追加 hash-sealed snapshot：完整闭合 15m bars、strategy commit/version、config hash、features、决策原因、candidate、风险输入和成本输入。
- 新增纯函数 replay/comparison，输出 decision / feature / TradeCandidate 三类匹配和 `FIRST_DIVERGENCE_POINT`。
- Opportunity 旁路追加 `ACTUAL`、`R1_SHADOW`、`R2_SHADOW`、`R3_SHADOW`，无执行副作用；R2 记录 expected cost_R。
- 确认的 reduce-only 真实平仓会从 exchange fill 回填四类 Shadow 的
  `gross_R`、`net_R`、`commission_R`、`funding_R`、`MFE_R`、`MAE_R`；部分减仓保持 pending，直到仓位真正 CLOSED。
- 新增 `scripts/verify_forward_baseline.py --min-cycles 100`。

## 真实验收状态（初始运行）

```text
STATUS: FORWARD_BASELINE_NOT_REPRODUCIBLE
Decision cycles captured: 0
Replay cycles: 0
Feature match rate: 0.0
Candidate match rate: 0.0
TradeCandidate match rate: 0.0
Immutable snapshot violations: 0
Shadow records: 0
Execution side effects: 0
```

运行库已完成 `0020 -> 0021` 增量迁移；100 次自然 cycle 尚未积累，因此禁止声称 Forward Baseline 已通过，也禁止进入策略优化阶段。

## 2026-08-14 复验结果

- 修复了首个真实 divergence：快照原先遗漏 `already_evaluated_bars`，重复决策在离线 replay 中会被重新计算为普通信号。快照现在同时记录该输入，以及 `execution_mode` / `engine_activation`；验收器只统计 `ACTIVE + BINANCE_TESTNET` 自然 cycles，Shadow 不会混入门槛。
- 官方 launcher 通过现有 Binance Testnet 代理重启，运行状态为 `ACTIVE / BINANCE_TESTNET / TESTNET_CANARY`，对账 `HEALTHY`。
- 当前 verifier：

```text
STATUS: FORWARD_REPRODUCIBLE_BASELINE_READY
Decision cycles captured: 144
Replay cycles: 144
Feature match rate: 1.0
Candidate match rate: 1.0
TradeCandidate match rate: 1.0
Immutable snapshot violations: 0
Shadow records: 32
Execution side effects: 0
Mismatch list: []
```

- 其中 7 个真实 opportunity 已产生 Shadow 记录；当前 2 个 Testnet 仓位仍为 `PROTECTED`，因此 `v2_shadow_outcomes` 仍为 `0`。未手工平仓、未制造成交、未进入 Strategy Plane 优化。

## 验证

- Forward Baseline focused tests: `4 passed`
- V2 scheduler/cycle focused tests: `67 passed`
- Ruff touched modules: `All checks passed!`
- Mypy touched modules: `Success: no issues found in 7 source files`
- Full pytest: `1598 passed, 16 skipped, 2 warnings`
- Full mypy: `Success: no issues found in 249 source files`
- Full Ruff: blocked only by pre-existing `scripts/verify_gate17_e2e.py:77` C416.

## 本次增量验证

- Forward Baseline + scheduler integration tests: `22 passed`
- Full pytest: `1608 passed, 7 skipped, 7 warnings`
- Touched Ruff: `All checks passed!`
- Mypy (`services apps shared scripts/verify_forward_baseline.py`): `Success: no issues found in 250 source files`
- Full Ruff: same pre-existing `scripts/verify_gate17_e2e.py:77` C416
- Full mypy (`.`): blocked by pre-existing duplicate module `check_positions.py` / archived copy
