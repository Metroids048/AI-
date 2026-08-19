# Final Project Closeout And Edge V2

日期：2026-08-20

本次只处理工程遗留、验证方法学审计和既有候选的 Edge 收口。V2 自动交易
Hot Path 没有新增改动；Natural Testnet 闭环继续以既有真实证据为准。

## Engineering Closeout

- `mypy`：PASS，`Success: no issues found in 274 source files`。范围由
  `pyproject.toml [tool.mypy].files` 明确限定为 `shared/services/apps`；archive
  不参与类型检查，没有使用 `ignore_errors = true`。
- `ruff check .`：PASS，`All checks passed!`。
- `pytest -q`：`1782 passed, 16 skipped, 2 warnings`。
- `verify_v2_transaction_contract.py`：退出码 0；当前冻结合同有效。
- `git diff --check`：PASS。

### Incidents

当前数据库中仍保留两条历史 `PROTECTION_RECOVERY_FAILED`（MEDIUM）：

| incident | created_at | 当前判断 |
|---|---|---|
| `a99e0461-d34e-42b8-aea5-ba4504ac283e` | 2026-08-10 12:56:51 | `HISTORICAL_EVIDENCE_ONLY` |
| `f369493a-4beb-4a3e-b5f8-0d41f0dfb413` | 2026-08-14 15:32:03 | `HISTORICAL_EVIDENCE_ONLY` |

后续真实生命周期证据显示 41/41 受管仓为 `CLOSED`，保护记录为
`37 PROTECTION_FILLED + 4 PROTECTION_CANCELLED`，最新对账为 `HEALTHY`，
交易所/本地开放仓位为 `0/0`。因此当前没有未解释的 P0/P1 暴露；两条原始
incident 行不删除、不伪造关闭，保留为历史审计记录。现有自动 resolution
contract 只会关闭 mismatch quarantine 类型，不能把这两条保护恢复事故静默改写
为已关闭。

## Strategy Readiness Method

| 项目 | 结论 | 证据 |
|---|---|---|
| `NEXT_BAR_PARITY` | `ALREADY_SATISFIED` | replay closed-bar -> next-bar-open tests |
| `FUNDING_POINT_IN_TIME` | `ALREADY_SATISFIED` | `ReplayCostModel` settlement-point lookup |
| `SPREAD_COST` | `EXTERNAL_EVIDENCE_REQUIRED` | 当前 artifact 仍标记 `ASSUMED` |
| `LATENCY_COST` | `EXTERNAL_EVIDENCE_REQUIRED` | 当前 artifact 仍标记 `ASSUMED` |
| `PARTIAL_FILL_COST` | `EXTERNAL_EVIDENCE_REQUIRED` | 当前 artifact 仍标记 `ASSUMED` |
| `WALK_FORWARD_LEDGER` | `ALREADY_SATISFIED` | `ProposalWalkForwardWindow` + append-only `TrialLedger` |
| `DEPENDENT_BOOTSTRAP` | `ALREADY_SATISFIED` | `stationary_cluster_bootstrap_lcb()` 已实现并有回归测试 |
| `FINAL_HOLDOUT_GUARD` | `ALREADY_SATISFIED` | `FinalHoldoutGuard` 拒绝越界读取 |
| `TRIAL_BUDGET` | `ALREADY_SATISFIED` | `ResearchTrialRegistry` + immutable pre-result registration |
| `LOOKAHEAD_ANALYSIS` | `ALREADY_SATISFIED` | 已有 research-only Freqtrade evidence |
| `RECURSIVE_ANALYSIS` | `ALREADY_SATISFIED` | 已有 research-only Freqtrade evidence |

代码级方法学结论为 `READY`；剩余阻塞是外部执行成本样本，不再重复建设
bootstrap、Promotion Gate 或撮合引擎。

## Edge Audit

本次只复核 `volatility_expansion_v1` 和 `breakout_retest_v1` 的既有不可变
artifact，不重新扫描其他 strategy family，也不访问 Final Holdout。

### `volatility_expansion_v1`

- reported trades：453
- unique trades：453（`proposal_id` 去重后 0 duplicates）
- effective sample size：453（当前 artifact 没有 overlapping duplicate）
- win rate：39.7351%
- profit factor：1.144345
- net expectancy：0.001324885
- max drawdown：22.9336%
- positive windows：6/8
- cost stress：+5 bps/side PF 1.049003、expectancy 0.000474885；
  +10 bps/side PF 0.963235、expectancy -0.000375115。

该候选有薄的开发期 edge，但没有达到现行 Promotion Gate（PF >= 1.35、
expectancy LCB > 0、成本观察完整且优于 Canary）。

### `breakout_retest_v1`

- reported/unique trades：31/31
- profit factor：1.082597
- net expectancy：0.000531709
- max drawdown：11.1639%
- positive windows：3/8
- cost stress：+5 bps/side PF 0.954258、expectancy -0.000318291。

该候选未达到 60 trades、5/8 positive windows 和 PF 门槛，直接淘汰。

本轮不新增第 7 个 variant；既有候选证据已经足以得出保守结论。

## Final State

```yaml
engineering_closeout:
  stale_open_items: 0
  mypy: PASS (274 source files)
  unresolved_incidents: 2 historical MEDIUM rows, no current P0/P1 exposure
  hotpath_contract: PASS

methodology:
  code_level: READY
  external_execution_costs: EXTERNAL_EVIDENCE_ONLY

research:
  trials_used_this_closeout: 0
  main_candidate: volatility_expansion_v1
  backup_candidate: breakout_retest_v1
  best_variant: none

production:
  authorization: PENDING
  entry_authority: NONE

final_head: dd06d60
remote_head: dd06d60
```

## Final Decision

`PROJECT_CLOSEOUT: NO_VALIDATED_EDGE`

这不是自动交易链失败。自动开平仓 Hot Path 已冻结并有真实 Binance Testnet
Natural evidence；当前只是不把成本敏感、LCB 未通过的开发期 edge 提升为
`READY_FOR_TESTNET_FORWARD`。
