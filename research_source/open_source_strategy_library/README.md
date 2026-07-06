# Open Source Strategy Library

This module is an E-level research-data intake path for open-source quant and crypto projects.

It does **not** import external trading code into the execution path. Sources are registered as
`StrategySourceManifest`, distilled into local RAG assets, then converted into conservative
`StrategyIdea` seeds. Any idea must still pass the platform chain:

`StrategyIdea -> StrategyDraft -> Strategy -> BacktestRun -> PaperRun -> PaperOrder -> Gatekeeper`.

## First-Batch Sources

- Freqtrade / Jesse / Hummingbot: crypto strategy shapes.
- ABU / vn.py / Lean: research framework and strategy taxonomy.
- TradingAgents / TradingAgents-CN / Vibe-Trading / Qbot / daily_stock_analysis: LLM research/review workflow only.
- Superalgos: visual crypto strategy and paper-trading workflow reference.

GPL/AGPL sources are research references only. Runtime code is not copied into platform services.

## Asset Layout

Each imported source writes traceable local assets under `assets/<source_id>/`:

- `source_summary.md`: local boundary and source-role summary.
- `asset_manifest.json`: URL, commit/ref, license, sha256, byte size, status, and extraction tags.
- `docs/*.md`: distilled README/docs material.
- `strategy_shapes/*.md`: distilled strategy-shape notes.
- `workflow_notes/*.md`: distilled research, optimization, data, or agent workflow notes.

`fetch_remote=true` uses an allowlist in `manifests/seed_sources.json`. Failed remote paths are recorded as
`failed_assets` instead of being hidden or converted into strategy ideas.
