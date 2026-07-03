# Open Source Strategy Library

This module is an E-level research-data intake path for open-source quant and crypto projects.

It does **not** import external trading code into the execution path. Sources are registered as
`StrategySourceManifest`, summarized into local RAG assets, then converted into conservative
`StrategyIdea` seeds. Any idea must still pass the platform chain:

`StrategyIdea -> StrategyDraft -> Strategy -> BacktestRun -> PaperRun -> PaperOrder -> Gatekeeper`.

## First-Batch Sources

- Freqtrade / Jesse / Hummingbot: crypto strategy shapes.
- ABU / vn.py / Lean: research framework and strategy taxonomy.
- TradingAgents / TradingAgents-CN / Vibe-Trading / Qbot / daily_stock_analysis: LLM research/review workflow only.
- Superalgos: visual crypto strategy and paper-trading workflow reference.

GPL/AGPL sources are research references only. Runtime code is not copied into platform services.
