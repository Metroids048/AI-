# FINAL RUNTIME + TRADE LIFECYCLE CLOSEOUT

日期：2026-08-19
分支：`backup/2026-08-10-wip`

## 本轮事实

- 已将 manual baseline 处理为生命周期状态：`BASELINE_MATCHED`、`MANUAL_BASELINE_DRIFT`、`MANUAL_BASELINE_ACK_REQUIRED`、`BASELINE_REFRESHED`。手动仓变化只按 symbol 阻断自动新开仓；没有执行 rebaseline、平仓、下单或取消订单。
- Runtime 四个接口共用 server-side `projection_id` 和 ownership-aware projection；canonical reconciliation service 是唯一 reconciliation gate。缺失 V2 order/fill identity 的本地行保持 fail-closed，不再回退到第二套 reconciliation gate。
- projection fingerprint 覆盖 V2 order/fill/protection identity；`/positions` 和 `/snapshot` 使用同一 projection 中的本地/保护事实；scheduler 状态持久化 baseline lifecycle 与 drift keys。
- 只读 trade lifecycle forensics 生成 30 个 episode：30/30 的 stop source 为 `PCT_FLOOR_0.35%`，15 个 TARGET，8 个 `DIRECTION_FAILURE`，7 个因四小时数据不足 `UNCLASSIFIED_INSUFFICIENT_DATA`。用户指定 ETH `1903.21/5.354` 与 SOL `76.30/85.09` 不在当前 cohort，不能伪造为已确认 stopped-then-recovered。

## Gate

- `RUNTIME_DEPLOYMENT_GATE`: `BLOCKED`。当前持久化 BTC manual baseline `0.5346 short` 与交易所当前空仓不一致；没有进行显式 rebaseline，也没有真实 ACTIVE closeout 证据。Chrome 控制插件缺少 `browser-service.mjs`，UI 验收未完成。
- `STRATEGY_ANALYSIS_GATE`: `READY`。尸检报告为只读，不读取 holdout，不改变执行参数。
- `STRATEGY_DEPLOYMENT_GATE`: `BLOCKED`。没有部署任何 variant；未修改 stop、sizing、leverage、fee 或 promotion gate。

## 验证

- 定向 Runtime/reconciliation/baseline/forensics：`95 passed`（后续核心复验 `57 passed`）。
- `ruff check .`：`All checks passed!`
- `mypy`：`Success: no issues found in 258 source files`
- 前端 Vitest：`114 passed`
- 前端生产构建：通过；保留既有 bundle size warning。
- 全量 pytest：`1710 passed, 16 skipped, 1 failed`；唯一失败为既有 `tests/services/test_daily_review.py::test_daily_review_keeps_all_terminal_reasons_for_same_symbol`。
- `NO LIVE TRADING PERFORMED`。

## 未完成与下一步

1. 由操作员明确确认 BTC 手动基线变化后执行安全 rebaseline，再重启并观察 ACTIVE/五币 Canary 的自然 scheduler cycles。
2. 获取精确 ETH/SOL closed episode 或继续积累真实可核验 cohort；随后才做 stop/entry coupled replay，禁止直接改 0.35% 或 sizing。
3. 修复或安装 control-chrome 内部依赖后再做浏览器验收；不能用代码审查替代。

中央知识同步脚本在本机不存在（配置仍指向旧的 `C:\\Users\\win` 路径），因此本轮只更新项目内记忆与会话记录，并在交付中报告该阻塞。
