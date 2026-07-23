# Recent Change Map

审计最近 30 个 commit（`git log -30 --date=iso-strict`）。重点变化如下：

| commit | 主题 | 审计影响 |
|---|---|---|
| `ff542f8` | C+ strategy、scheduler coordination、ensemble weighting、audit tooling | 当前 HEAD；多模块同时变化，基线不可归因 |
| `255420b` | portable runtime ledger | 增加第二账本，需区分运行真源 |
| `bad4dd1` | config snapshot + Testnet authorization | 配置快照开始参与周期，形成覆盖链 |
| `9745089` | execution blocker checks、TradeIntent normalizer、config snapshot 等 46 文件 | `gateway.submit_order` 新增 `market_rules_snapshot` fail-closed 要求；账本 17 次执行失败与之完全匹配 |
| `e76726b` | execution blocker / testnet acceptance tooling | 增加门禁与接受路径，未证明生产自动路径已连通 |
| `c3da32c` | reconcile Binance simulation from exchange truth | 外部仓位恢复路径改动，需校验来源绑定 |
| `27ef77b`、`d8c3213` | reproducer tests for pending fill / auto-link blockers | 历史失败模式已有测试材料，但不能替代运行时证据 |

当前 `git status` 仅见既存 `docs/audit/shadow-ablation-report.md`、`docs/audit/strategy-liveness-funnel.md` 修改以及本目录新报告；本轮未改业务代码。
