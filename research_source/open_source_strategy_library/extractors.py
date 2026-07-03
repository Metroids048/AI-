"""Extract platform StrategyIdea seeds from open-source strategy manifests."""

from __future__ import annotations

from shared.models import Market, StrategyIdea, StrategySourceManifest


class OpenSourceStrategyExtractor:
    """Convert source manifests into conservative, rule-first StrategyIdea seeds."""

    def extract_ideas(self, manifest: StrategySourceManifest, *, max_ideas: int | None = None) -> list[StrategyIdea]:
        ideas: list[StrategyIdea] = []
        role = manifest.project_role
        categories = set(manifest.asset_categories)

        if manifest.source_id in {"freqtrade", "jesse"} or role == "crypto_strategy_shapes":
            ideas.append(self._trend_following_idea(manifest))
        if manifest.source_id in {"hummingbot", "superalgos"} or {"market_making", "grid"} & categories:
            ideas.append(self._grid_market_making_idea(manifest))
        if manifest.source_id in {"freqtrade", "hummingbot", "jesse"} or "arbitrage" in categories:
            ideas.append(self._funding_carry_idea(manifest))
        if role in {"research_framework", "llm_research_workflow", "research_framework_candidate"}:
            ideas.append(self._research_note_idea(manifest))

        deduped = list({idea.title: idea for idea in ideas}.values())
        if max_ideas is not None:
            return deduped[:max_ideas]
        return deduped

    def _base_source_ref(self, manifest: StrategySourceManifest) -> str:
        return f"{manifest.repo_url}#{manifest.source_id}"

    def _funding_carry_idea(self, manifest: StrategySourceManifest) -> StrategyIdea:
        return StrategyIdea(
            title=f"{manifest.name} seeded funding-rate carry",
            source=f"open_source:{manifest.source_id}",
            market=Market.CRYPTO_PERP,
            symbol_scope=["BTC/USDT", "ETH/USDT"],
            hypothesis_summary=(
                "Use open-source crypto bot/arbitrage patterns as research context for a rules-first funding-rate "
                "carry strategy: enter only when funding exceeds threshold, hedge spot/perp exposure, model fees, "
                "slippage, funding net income, and require validation before Paper admission."
            ),
            source_ref=self._base_source_ref(manifest),
            rationale="First-batch strategy class: funding-rate arbitrage; must reuse carry validation and gatekeeper.",
            intake_bucket="rule_candidate",
        )

    def _trend_following_idea(self, manifest: StrategySourceManifest) -> StrategyIdea:
        return StrategyIdea(
            title=f"{manifest.name} seeded crypto trend following",
            source=f"open_source:{manifest.source_id}",
            market=Market.CRYPTO_PERP,
            symbol_scope=["BTC/USDT", "ETH/USDT"],
            hypothesis_summary=(
                "Convert mature crypto bot strategy shapes into a BTC/ETH trend-following candidate using EMA/MACD "
                "direction, ADX or volume confirmation, explicit ATR/structure stoploss, and fixed risk sizing."
            ),
            source_ref=self._base_source_ref(manifest),
            rationale="First-batch strategy class: trend following; no external strategy code is imported.",
            intake_bucket="rule_candidate",
        )

    def _grid_market_making_idea(self, manifest: StrategySourceManifest) -> StrategyIdea:
        return StrategyIdea(
            title=f"{manifest.name} seeded paper grid market making",
            source=f"open_source:{manifest.source_id}",
            market=Market.CRYPTO_PERP,
            symbol_scope=["BTC/USDT"],
            hypothesis_summary=(
                "Use market-making/grid concepts only for Paper simulation: quote symmetric levels around a reference "
                "price, enforce inventory bounds, stop when volatility or spread exceeds limits, and never promote "
                "to live without a dedicated risk design."
            ),
            source_ref=self._base_source_ref(manifest),
            rationale="First-batch strategy class: grid/market-making; Paper-only in this tranche.",
            intake_bucket="rule_candidate",
        )

    def _research_note_idea(self, manifest: StrategySourceManifest) -> StrategyIdea:
        bucket = "research_note_only" if manifest.project_role == "llm_research_workflow" else "metric_to_validate"
        return StrategyIdea(
            title=f"{manifest.name} research workflow reference",
            source=f"open_source:{manifest.source_id}",
            market=Market.CRYPTO_PERP,
            symbol_scope=["BTC/USDT"],
            hypothesis_summary=(
                "Use this project as research/RAG context for framework boundaries, strategy taxonomy, reports, "
                "or multi-agent review workflow. It does not produce trade direction, position size, or live orders."
            ),
            source_ref=self._base_source_ref(manifest),
            rationale="Architecture/RAG reference; requires later Strategy Agent rule extraction before validation.",
            intake_bucket=bucket,
        )
