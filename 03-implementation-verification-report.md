# 03 实施验证报告：Legacy Active Writer 根因修复

日期：2026-08-03
施工依据：冻结方案 v2.0；本地工作树 `main@493c7c6` 为事实基线。

## 结论

标准启动模式保持 `v2_shadow`。本轮修复的是该模式下仍拥有真实写权限的 legacy writer，没有切换 `v2_active`，也没有修改 V2 生产实现、启动器、数据库、策略指标或 Gatekeeper 门槛。

4 个冻结 P1 已实施：

- P-001：asset tier 优先；无 tier 时 operator `max_leverage` 优先于 strategy；operator `order_notional_usdt` 优先于 strategy notional；operator risk 优先于 strategy risk。
- P-002：sampling 保留 decision trace，但 `paper_order_should_trade=False`；flat 时返回 `skip_non_promotable_sampling`，已有反向仓位时只 hold，不创建订单或触发 opposite close。
- P-003：entry 在 `set_leverage` 失败时抛出 `LEVERAGE_CONFIGURATION_FAILED`，不会调用 `create_order`；close-only 跳过杠杆设置并继续退出。
- P-004：legacy 漂移默认值为 `20bps / 0.25 ATR`，保留 `max(fixed, ATR)` 与 `PRETRADE_PRICE_DRIFT` 语义。

## Active Runtime 门

- 分支与基线：`main@493c7c625acb85cf0352cd5b34bd9c86d2ff849e`，远端 `origin/main` 同提交。
- launcher：`scripts/launch-paper-console.ps1` 与 `scripts/run-local-paper-scheduler.py` 固定 `v2_shadow`。
- activation：`V2=SHADOW`、`allow_legacy_writer=True`。
- scheduler：同时解析 `paper_runtime_cycle` 与 `automated_trading_v2_cycle`。
- mismatch：非法 engine flag 显式 `ValueError`，不自动 remap。
- hook：`.git/hooks/pre-commit` 已安装。

## 完整链路证据

新增 `tests/integration/test_legacy_v2_shadow_execution_contract.py`，验证：

```text
production launcher contract (same test reads both launchers)
  -> v2_shadow activation
  -> RuntimeScheduler registers legacy + V2 shadow jobs
  -> execution-profile API
  -> NEXT_CYCLE ConfigSnapshot
  -> registered paper_runtime_cycle runner -> legacy PaperRuntimeService / Gatekeeper
  -> BinanceUsdtPerpetualGateway.submit_order
  -> set_leverage(7)
  -> entry create_order(quantity=1.23)
  -> exchange order id + trade id + stop/TP ids
  -> immutable fill receipt
  -> local position(entry_price=100.1)
```

同一测试中 strategy `3/500` 被 operator `7/123` 覆盖。真实 gateway 生产代码由无网络 fake CCXT client 驱动，验证了 payload、杠杆调用顺序、成交解析、保护单和本地投影；没有访问 Binance。

并行的 V2 SHADOW cycle 执行完整 decision funnel，但 entry、protection、position projection 均为 0。legacy sampling 的 gateway、execution order、position 均为 0；已有反向仓位不关闭。由此证明 `v2_shadow` 下只有 legacy 是 writer，且 sampling 不是 writer 授权来源。

## 验收矩阵

| 验收项 | 结果 |
| --- | --- |
| operator `7/123` 覆盖 strategy `3/500` | 通过 |
| asset tier 保持最高优先级 | 通过 |
| sampling trace 保留、订单/仓位/opposite close 为 0 | 通过 |
| primary entry、protective/risk/time/rank-dropout 退出不回归 | 通过 |
| leverage failure `create_order=0`，固定错误前缀 | 通过 |
| close-only `set_leverage=0`、`create_order=1` | 通过 |
| 80bps 拒绝、20bps 边界与 ATR 自适应 | 通过 |
| legacy freeze line ceiling | 通过：orchestrator `2667/2668`，signal `1178/1179` |

## 验证记录

```text
[验证] legacy 目标集   -> 114 passed, 2 warnings
[验证] V2/runtime 集  -> 63 passed
[验证] ruff check .   -> All checks passed!
[验证] mypy           -> Success: no issues found in 219 source files
[验证] pytest -q      -> 1282 passed, 16 skipped, 2 warnings in 105.14s
[基线对比]            -> 初始 1279 passed, 7 skipped, 7 warnings；本次无新失败
```

第一次全仓执行暴露 2 个 legacy line-ceiling 失败（其余 `1280 passed`）。实现随后收敛为原分支替换并删除冗余注释，没有抬高 ceiling；第二次全仓通过。这一过程作为测试完整性证据保留。

独立只读 reviewer 最初发现 orchestrator sampling 防线依赖 signal flag、scheduler contract 使用测试注入 runner、trace/零 close-order 断言不足。三项均已修复并复审；最终 verdict 为无剩余 finding。

## 修改边界

生产代码仅修改：

- `services/execution/paper_signal.py`
- `services/execution/paper_cycle_orchestrator.py`
- `services/execution/gateway.py`
- `shared/config.py`

其余变更仅为测试和本报告/运行日志/项目记忆。没有迁移、引擎 flag、mainnet、V2 生产实现或真实订单变更。

## 外部验收边界

本轮未执行 A-504，也没有新的真实 Binance Testnet order ID。完整代码链路已经由真实生产 gateway + fake exchange 契约验证，但这不等价于网络、凭据、Testnet 撮合或部署进程验收。按冻结方案，真实 Binance Testnet canary 需要另行明确授权，不能据本报告宣称外部交易所链路已验收。
