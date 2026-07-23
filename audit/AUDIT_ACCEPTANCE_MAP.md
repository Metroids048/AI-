# Audit Acceptance Map

| 完成标准 | 证据 | 状态 |
|---|---|---|
| 仓库/入口/最近 commit | `REPOSITORY_MAP.md`、`ENTRYPOINTS.md`、`RECENT_CHANGE_MAP.md` | PASS |
| 调用链与手动分叉 | 三份 call graph/diff | PASS（代码证据） |
| 门禁与配置 | `SILENT_DROP_INVENTORY.md`、CSV、三份 config 报告 | PASS（覆盖；非行为修复） |
| SQLite 只读账本 | 本报告引用 watermark/count，query_only=1 | PASS |
| 21 事件和数量守恒 | `OBSERVABILITY_GAPS.md`、`STAGE_COUNT_MODEL.md` | PARTIAL/UNOBSERVABLE |
| 测试基线 | `TEST_BASELINE.md`、failure evidence | PASS（结果已记录） |
| 三隔离通道 | `ISOLATED_TEST_DESIGN.md`、matrix | DESIGN ONLY |
| 最多三个根因 | `ROOT_CAUSE_REPORT.md` | PASS |
| 交易所因果完整核验 | 只读 order 可确认成交，无法从本地唯一绑定发起者 | PARTIAL |
