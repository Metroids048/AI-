from __future__ import annotations

import json

from research_source.open_source_strategy_library import OpenSourceStrategyExtractor, OpenSourceStrategyLibrary


def test_open_source_importer_is_idempotent_and_builds_rag_asset(tmp_path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "source_id": "freqtrade",
                    "name": "Freqtrade",
                    "repo_url": "https://github.com/freqtrade/freqtrade",
                    "license": "GPL-3.0",
                    "project_role": "crypto_strategy_shapes",
                    "asset_categories": ["crypto_bot", "strategy_templates"],
                    "crypto_relevance": "high",
                    "license_notes": "research reference only",
                }
            ]
        ),
        encoding="utf-8",
    )
    library = OpenSourceStrategyLibrary(seed_manifest_path=seed, asset_root=tmp_path / "assets")

    first = library.import_sources(source_ids=["freqtrade"], refresh_assets=True)
    second = library.import_sources(source_ids=["freqtrade"], refresh_assets=True)

    assert len(first.imported) == 1
    assert len(second.imported) == 1
    assert first.imported[0].ingestion_status == "imported"
    assert first.imported[0].rag_asset_refs == second.imported[0].rag_asset_refs
    assert (tmp_path / "assets" / "freqtrade" / "source_summary.md").exists()


def test_open_source_extractor_creates_first_batch_strategy_ideas() -> None:
    manifest = OpenSourceStrategyLibrary().get_source("hummingbot")
    assert manifest is not None

    ideas = OpenSourceStrategyExtractor().extract_ideas(manifest)
    buckets = {idea.intake_bucket for idea in ideas}
    titles = " ".join(idea.title.lower() for idea in ideas)

    assert "rule_candidate" in buckets
    assert "funding" in titles
    assert "grid" in titles or "market making" in titles
