# 审计补充：方向性开仓阻塞修复（幽灵仓 + ReduceOnly 死循环）

- Date: 2026-07-12
- Follows: `2026-07-12-directional-binance-reachability.md`
- Layer: Execution

## 根因（运行态复核）

方向性 run `457c6ecd` **已武装**，并非 lane 门禁永久拒绝。真实阻塞是：

1. **Demo 持仓被写入方向性 PaperRun**（`demo_audit.record_exchange_positions`），BTC/ETH 计入组合初始风险 → 大量新开仓 `portfolio_initial_risk_exceeded`。
2. **本地幽灵仓（如 SOL）**在交易所无仓时仍触发保护平仓；gateway 返回 `-2022 ReduceOnly Order is rejected` 后旧逻辑 **拒绝订单且保留本地仓**，每轮 cycle 重试，形成死循环。

## 代码修复

| 项 | 改动 |
|---|---|
| ReduceOnly 已平 | `_ensure_binance_execution`：close_only + `-2022` → 视为 `exchange_already_flat`，允许本地平仓收口 |
| Demo 同步边界 | `record_exchange_positions`：仅 audit run 全量镜像；策略/mature run 只清「非本策略 gateway 成交」的外来/幽灵仓 |
| 本地修复脚本 | `scripts/repair_directional_ghost_positions.py`（已对 `.local_paper_console.db` 清掉方向性 BTC/ETH/SOL） |

## 验证

- 聚焦 pytest：59 passed（paper_runtime / mirror_lane / exit_ladder / gatekeeper / gateway / paper_runtime_api）
- 幽灵修复：cleared BTC/ETH/SOL on `457c6ecd`
- 运维收口：API `http://127.0.0.1:8016` `/health` ok；仅 mature 方向性 `457c6ecd` running，重复 run `c2b5a1fa` paused
- 机械镜像证明：`scripts/_prove_directional_mirror.py` → LINK open `813722666` / reduceOnly close `813722823`，verdict `directional_mirror_ok`（`docs/audits/_directional_manual_mirror_proof.json`）
- 自动 cycle：已跑通，多为信号过滤跳过（`multi_timeframe_disagreement` / `ensemble_discarded`），非 gateway 崩溃

## 仍未宣称

- 不宣称方向性策略已在 Testnet 连续自动开仓或稳定盈利
- OOS / ExitLadder 复测未过门槛，不改自动准入、不开主网
