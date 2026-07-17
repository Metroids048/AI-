# AI Quant Research Platform

本仓库实现一套持续生成、验证、淘汰和迭代交易策略的 AI 量化研究平台，不是荐股或跟单机器人。

## Current Entry Points

- 当前运行事实：[CURRENT_STATE.md](CURRENT_STATE.md)
- 架构真源：[AI_Quant_Research_Platform_完整报告.docx](AI_Quant_Research_Platform_完整报告.docx)
- 当前交易链路：[docs/architecture/current-trading-pipeline.md](docs/architecture/current-trading-pipeline.md)
- 文档索引：[docs/README.md](docs/README.md)
- 受支持脚本：[scripts/SUPPORTED.md](scripts/SUPPORTED.md)
- 协作规则：[AGENTS.md](AGENTS.md)

## Active Research Scope

- Markets: BTC/USDT, ETH/USDT, SOL/USDT perpetuals.
- Scheduled lanes: evidence-gated directional research and local-only signal observation.
- Validation order: historical OOS replay -> Paper/Binance Simulation -> small-capital live only after renewed validation.
- Mainnet remains disabled.

## Start And Verify

```powershell
.\一键启动.cmd
agent-python -m scripts.verify_runtime_config_sync --database-url sqlite:///.local_paper_console.db
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_paper_console.db --lookback-days 7
```

Real-time heartbeats only retain recent candles. Historical research data must be
backfilled into the same Paper database before evidence computation. Both
commands require an explicit `--database-url`; omitting it is a hard error so a
separate validation database cannot be used accidentally.

Backfill one year of BTC/ETH/SOL research candles into the local Paper database:

```powershell
agent-python -m scripts.run_top20_technical_validation `
  --days 365 `
  --database-url sqlite:///C:/Users/win/Desktop/AI--main/.local_paper_console.db `
  --output artifacts/backfill/top3-technical-validation.md
```

Compute local-data-only evidence after historical 15m/1h/4h data is complete:

```powershell
agent-python -m scripts.compute_signal_edge_stats `
  --strategy-key auto_paper_mature_templates `
  --database-url sqlite:///C:/Users/win/Desktop/AI--main/.local_paper_console.db `
  --days 365
```

## Engineering Checks

```powershell
agent-python -m pytest -q -m "not integration"
agent-python -m ruff check .
agent-python -m mypy
npm --workspace frontend/admin run test
npm run admin:build
```

Historical diagnoses and handoffs are preserved under `docs/archive/` but are not current sources of truth.
