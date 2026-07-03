from __future__ import annotations

from shared.models import StrategySourceManifest


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
        license_notes="research reference only",
    )

    assert manifest.source_id == "freqtrade"
    assert manifest.license == "GPL-3.0"
    assert manifest.project_role == "crypto_strategy_shapes"
    assert manifest.rag_asset_refs[0].endswith("source_summary.md")
    assert "research" in (manifest.license_notes or "")
