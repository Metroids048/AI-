from __future__ import annotations

from shared.models import ResearchSourceAsset, StrategySourceManifest


def test_strategy_source_manifest_captures_license_and_rag_refs() -> None:
    manifest = StrategySourceManifest(
        source_id="freqtrade",
        name="Freqtrade",
        repo_url="https://github.com/freqtrade/freqtrade",
        license="GPL-3.0",
        project_role="crypto_strategy_shapes",
        asset_categories=["strategy_templates"],
        crypto_relevance="high",
        rag_asset_refs=["research_source/open_source_strategy_library/assets/freqtrade/source_summary.md"],
        license_policy="distilled_research_only",
        asset_allowlist=[
            {"path": "README.md", "asset_type": "documentation", "extraction_tags": ["backtesting"]}
        ],
        strategy_extraction_targets=["trend_following"],
        license_notes="research reference only",
    )

    assert manifest.source_id == "freqtrade"
    assert manifest.license == "GPL-3.0"
    assert manifest.project_role == "crypto_strategy_shapes"
    assert manifest.rag_asset_refs[0].endswith("source_summary.md")
    assert "research" in (manifest.license_notes or "")
    assert manifest.asset_allowlist[0]["path"] == "README.md"


def test_research_source_asset_captures_traceability_fields() -> None:
    asset = ResearchSourceAsset(
        asset_id="freqtrade:abc123",
        source_id="freqtrade",
        asset_type="documentation",
        origin_url="https://github.com/freqtrade/freqtrade/blob/abc/README.md",
        origin_ref="abc",
        license="GPL-3.0",
        local_path="research_source/open_source_strategy_library/assets/freqtrade/docs/README.md",
        sha256="a" * 64,
        bytes=120,
        extraction_tags=["backtesting"],
        summary="Backtesting and dry-run workflow.",
    )

    assert asset.source_id == "freqtrade"
    assert asset.local_path.endswith("README.md")
    assert asset.extraction_tags == ["backtesting"]
