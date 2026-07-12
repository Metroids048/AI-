# 审计：方向性策略 Binance Testnet 触达确认

- Date: 2026-07-12
- Scope: Plan Phase A（只读，不改业务逻辑）
- Layer: Execution Layer 运行态证据
- Local DB: `.local_paper_console.db`
- Env smoke: `scripts/_probe_directional_reachability.py` + Testnet account probe

## 结论（三选一）

**仅本地记账**

方向性自动 paper run 已武装且全局 env 允许镜像，但该 run 下近期订单 **没有** `gateway_order_id`。  
Testnet 上可见的成交/持仓，主要来自 **验收脚本路径**（`acceptance_action` / 无 `decision_pipeline`），不能证明方向性 DecisionPipeline 自动开仓已触达交易所。

## 1. 代码路径（当前，非旧诊断文档）

### `_should_execute_on_binance`（`services/execution/paper_runtime.py` ~1417–1441）

镜像总开关需同时满足：

- `execution_mode == binance_simulation_first` 或 `mirror_to_gateway`
- `cost_gate_verified == True`
- `BINANCE_AUTO_EXECUTE` / `BINANCE_USE_TESTNET` / 非 live / gateway 非空

对新开仓订单（非 `close_only`）：

- `strategy_lane == "carry"` → 仍要求 `estimated_net_edge_bps >= min_estimated_net_edge_bps`
- **否则（方向性）** → `pipeline_status` 真值且无 `rejection_codes` 即放行

旧诊断里「非 carry 永远 False」的说法 **已过时**；当前代码已放宽方向性分支。

### `_trace`（`services/execution/decision_pipeline.py`）

仍 **不写入** `strategy_lane`。采样 keys：`pipeline_status`, `signals`, `ensemble`, `meta_label`, `veto_result`, `volatility`。  
Carry 仍在 `paper_signal.py` 显式写 `strategy_lane: "carry"`。

### `default_mirror_to_gateway()`（`bootstrap.py`）

返回 `False`：新建自动 run 默认本地，须显式武装。

## 2. 运行态证据

### 全局 env

| 项 | 值 |
|---|---|
| `binance_auto_execute` | true |
| `binance_use_testnet` | true |
| `live_trading_enabled` | false |
| credentials | configured |
| Testnet probe | connected, trading_mode=demo, position_count=3 |

### Paper runs（`.local_paper_console.db`）

| paper_run_id | strategy_lane | execution_mode | mirror | cost_gate_verified |
|---|---|---|---|---|
| `457c6ecd-…` | directional | binance_simulation_first | true | true |
| `fdf5a18f-…` | carry | binance_simulation_first | true | true |
| `c2b5a1fa-…` | directional | null | false | null |
| `6297d56f-…` | null | null | false | null（验收类订单归属） |

方向性主 run `457c6ecd`：**已武装**。

### Gateway 订单归属（最近 200 笔有 `gateway_order_id`）

| 归属 | 数量 | 有 decision_pipeline | 解读 |
|---|---|---|---|
| `6297d56f-…` | 65 | 0 | 验收/补偿路径（含 `acceptance_action`） |
| `3ab4cd1d-…` | 13 | 13 | 有 pipeline，但 run 无 directional lane 标签 |
| `fdf5a18f-…` carry | 1 | 1 | carry 镜像 |
| 方向性 `457c6ecd-…` | **0** gateway | 本地有 pipeline 痕迹 | **未提交到交易所** |

`strategy_lane=directional` 在订单 trace 中出现次数：**0**（与 `_trace` 不写该字段一致）。

## 3. 综合判断

| 问题 | 答案 |
|---|---|
| 代码上方向性新开仓能否镜像？ | 能（在武装 + `pipeline_status` + 无 rejection 时） |
| 当前方向性自动 run 是否武装？ | 是（`457c6ecd`） |
| 该 run 是否已在 Testnet 留下方向性开仓？ | **否**（0 `gateway_order_id`） |
| 「模拟盘跑通」更像什么？ | 验收脚本 20 币成交 +/或 carry；不是方向性自动 cycle |

## 4. 后续（不在本阶段改）

1. Phase B：`_trace` 写入 `strategy_lane`（默认 `directional`），便于订单溯源。  
2. 排查方向性 run 有本地订单却无 gateway 的原因（历史未武装窗口、当时 `pipeline_status` 缺失、或 gateway 提交失败）。  
3. Validation OOS 未过门槛前，不把机械触达当成策略准入。

## 原始探针产物

- `docs/audits/_phase_a_probe_raw.json`
- `docs/audits/_phase_a_sqlite_raw.json`
- `docs/audits/_phase_a_order_provenance.json`
