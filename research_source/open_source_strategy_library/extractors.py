"""Extract platform StrategyIdea seeds from open-source strategy manifests."""

from __future__ import annotations

from pathlib import Path

from shared.models import Market, StrategyIdea, StrategySourceManifest

from .asset_index import REPO_ROOT


class OpenSourceStrategyExtractor:
    """Convert source manifests into conservative, rule-first StrategyIdea seeds."""

    def extract_ideas(self, manifest: StrategySourceManifest, *, max_ideas: int | None = None) -> list[StrategyIdea]:
        asset_refs = _asset_refs(manifest)
        asset_text = _load_asset_text(asset_refs)
        if not asset_refs:
            return [self._research_note_idea(manifest, asset_refs=[], asset_text="")]
        if manifest.license.lower() == "unknown" or manifest.license_policy == "metadata_only":
            return [self._research_note_idea(manifest, asset_refs=asset_refs, asset_text=asset_text)]

        ideas: list[StrategyIdea] = []
        targets = set(manifest.strategy_extraction_targets)
        role = manifest.project_role
        categories = set(manifest.asset_categories)

        if (
            manifest.source_id in {"freqtrade", "jesse", "abu"}
            or role == "crypto_strategy_shapes"
            or "trend_following" in targets
            or "price_action" in targets
        ):
            ideas.append(self._trend_following_idea(manifest, asset_refs=asset_refs, asset_text=asset_text))
        if (
            manifest.source_id in {"hummingbot", "superalgos"}
            or {"market_making", "grid"} & categories
            or {"grid_market_making", "market_making"} & targets
        ):
            ideas.append(self._grid_market_making_idea(manifest, asset_refs=asset_refs, asset_text=asset_text))
        if (
            manifest.source_id in {"freqtrade", "hummingbot", "jesse"}
            or "arbitrage" in categories
            or "funding_carry" in targets
        ):
            ideas.append(self._funding_carry_idea(manifest, asset_refs=asset_refs, asset_text=asset_text))
        if role in {"research_framework", "llm_research_workflow", "research_framework_candidate"} or (
            targets - {"trend_following", "grid_market_making", "market_making", "funding_carry", "price_action"}
        ):
            ideas.append(self._research_note_idea(manifest, asset_refs=asset_refs, asset_text=asset_text))

        deduped = list({idea.title: idea for idea in ideas}.values())
        if max_ideas is not None:
            return deduped[:max_ideas]
        return deduped

    def _base_source_ref(self, manifest: StrategySourceManifest) -> str:
        return f"{manifest.repo_url}#{manifest.source_id}"

    def _metadata(
        self,
        manifest: StrategySourceManifest,
        *,
        asset_refs: list[str],
        extraction_tags: list[str],
        asset_text: str,
    ) -> dict:
        return {
            "source_manifest_ref": manifest.source_id,
            "asset_refs": asset_refs,
            "license": manifest.license,
            "license_policy": manifest.license_policy,
            "extraction_tags": extraction_tags,
            "evidence_preview": " ".join(asset_text.split())[:500],
        }

    def _funding_carry_idea(
        self,
        manifest: StrategySourceManifest,
        *,
        asset_refs: list[str],
        asset_text: str,
    ) -> StrategyIdea:
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
            intake_metadata=self._metadata(
                manifest,
                asset_refs=asset_refs,
                extraction_tags=["funding_carry", "arbitrage", "validation_required"],
                asset_text=asset_text,
            ),
            intake_bucket="rule_candidate",
        )

    def _trend_following_idea(
        self,
        manifest: StrategySourceManifest,
        *,
        asset_refs: list[str],
        asset_text: str,
    ) -> StrategyIdea:
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
            intake_metadata=self._metadata(
                manifest,
                asset_refs=asset_refs,
                extraction_tags=["trend_following", "technical_signal", "risk_sizing"],
                asset_text=asset_text,
            ),
            intake_bucket="rule_candidate",
        )

    def _grid_market_making_idea(
        self,
        manifest: StrategySourceManifest,
        *,
        asset_refs: list[str],
        asset_text: str,
    ) -> StrategyIdea:
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
            intake_metadata=self._metadata(
                manifest,
                asset_refs=asset_refs,
                extraction_tags=["grid_market_making", "inventory_risk", "paper_only"],
                asset_text=asset_text,
            ),
            intake_bucket="rule_candidate",
        )

    def _research_note_idea(
        self,
        manifest: StrategySourceManifest,
        *,
        asset_refs: list[str],
        asset_text: str,
    ) -> StrategyIdea:
        bucket = (
            "research_note_only"
            if manifest.project_role == "llm_research_workflow"
            or manifest.license.lower() == "unknown"
            or manifest.license_policy == "metadata_only"
            else "metric_to_validate"
        )
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
            intake_metadata=self._metadata(
                manifest,
                asset_refs=asset_refs,
                extraction_tags=["research_workflow", "rag_context", "no_direct_orders"],
                asset_text=asset_text,
            ),
            intake_bucket=bucket,
        )


def _asset_refs(manifest: StrategySourceManifest) -> list[str]:
    refs = list(manifest.rag_asset_refs)
    rag_index = manifest.metadata.get("rag_index")
    if isinstance(rag_index, dict):
        refs.extend(str(item.get("path")) for item in rag_index.get("assets", []) if item.get("path"))
    return list(dict.fromkeys(refs))


def _load_asset_text(asset_refs: list[str]) -> str:
    chunks: list[str] = []
    for ref in asset_refs[:8]:
        path = Path(ref)
        if not path.is_absolute():
            path = REPO_ROOT / ref
        if path.exists() and path.suffix.lower() == ".md":
            chunks.append(path.read_text(encoding="utf-8", errors="ignore")[:4000])
    return "\n\n".join(chunks)
