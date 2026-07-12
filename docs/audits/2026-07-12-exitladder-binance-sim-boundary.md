# 审计：ExitLadder / 相关性 / 对账解耦 / 币安模拟盘连通边界

- Date: 2026-07-12
- Scope: Prompt 4–6 实现收尾 + 币安模拟盘机械链路边界说明
- Layer: Validation Layer 前置证据不变；Execution Layer 仅补齐 Paper/Testnet 机械能力

## 结论（先读）

1. **分层复测仍未过门槛**（见 `2026-07-12-top20-technical-validation.md` 与 Prompt 2 复跑结果）。因此：
   - **未**修改任何策略的 `default_enabled_for_auto_trading`
   - **未**放开主网 / `live_trading_enabled`
   - **未**把预筛失败包装成“策略已盈利”
2. 本轮交付的是 **出场阶梯、相关性收紧、对账解耦、模拟盘镜像机械链路**；它们服务研究闭环中的 Execution/Risk 机械正确性，**不构成策略准入证据**。

## 本轮落地

| 项 | 落点 | 行为 |
|---|---|---|
| ExitLadder | `services/execution/exit_ladder.py` + `paper_runtime.py` | 默认 1.0R 平 40%→保本；1.5R 平 30%→锁 L1；剩余 `remainder_trail_after_r=2.5` |
| 默认规则 | `AUTO_PAPER_TECHNICAL_RULES.takeprofit_rules` | 写入 `exit_ladder`；无 ladder 时保留旧 `partial_close_fraction` 路径 |
| 币安部分平 | `gateway.submit_order` reduceOnly + `refresh_protection_orders` | 部分平后按剩余数量重挂 STOP；失败 fail-closed 记审计，不静默漂移 |
| 相关性 | `paper_signal._build_risk_state` + `gatekeeper` | corr>0.7 按 `1-corr` 打折；≥2 个高相关同向持仓 → `correlated_exposure_limit_exceeded` |
| 对账解耦 | `paper_runtime._reconcile_local_positions_with_exchange` | 每次 cycle 先对账；入场仍按 `cycle_key` idempotent；交易所已平则本地平仓 |

## 币安模拟盘连通冒烟口径

机械链路启用条件（全部满足才镜像）：

- `execution_profile.mirror_to_gateway` 或 `execution_mode=binance_simulation_first`
- `cost_gate_verified=True`
- `BINANCE_AUTO_EXECUTE=true`
- `BINANCE_USE_TESTNET=true`
- `LIVE_TRADING_ENABLED=false`
- 网关实例可用

冒烟脚本：`scripts/smoke_binance_simulation_path.py`（只读探测连通与门禁；**不**下主网单、**不**改策略开关）。

## 明确不做

- 不因 OOS 净期望为负而宣称自动策略可实盘
- 不启用主网
- 不混改前端 UI
