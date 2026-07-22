# Runtime Ledger（可版本化交易证据）

本目录存放**近 30 天** Paper / Simulation 交易与决策快照，供换设备、代码审查后继续分析。

热库 `.local_paper_console.db` 仍是本机运行真相源；本目录是**可提交的只读副本**（已脱敏），不是实盘下单通道。

## 文件

| 路径 | 含义 |
|------|------|
| `current/manifest.json` | 导出元数据：时间窗、行数、源库指纹 |
| `current/SUMMARY.md` | 给人看的摘要（订单/决策/持仓计数） |
| `current/ledger.sqlite.gz` | 可分析的 SQLite 快照（gzip） |

## 操作员流程

在跑盘机器上（默认读 `.local_paper_console.db`）：

```text
agent-python -m scripts.export_runtime_ledger
git add docs/evidence/runtime-ledger/current
git commit -m "Refresh 30-day runtime ledger snapshot."
git push
```

在另一台设备 `git pull` 之后：

```text
agent-python -m scripts.import_runtime_ledger
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_runtime_ledger.db --lookback-days 30
```

也可不 import，直接：

```text
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///docs/evidence/runtime-ledger/current/_tmp.sqlite --lookback-days 30
```

（需先 `import` 解压，或使用 import 生成的 `.local_runtime_ledger.db`。）

## 安全边界

- 导出时会递归剥离 JSON 字段中的 `api_key` / `secret` / `token` 等键。
- 禁止把 `.env` 或热库整文件提交进 Git。
- 本快照**不**替代 Validation Layer；观察通道成交仍不算策略准入证据。
