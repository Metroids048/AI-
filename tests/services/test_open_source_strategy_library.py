from __future__ import annotations

import json
from pathlib import Path

from research_source.open_source_strategy_library import OpenSourceStrategyExtractor, OpenSourceStrategyLibrary


class FakeRemoteFetcher:
    def resolve_ref(self, repo_url: str) -> str:
        return "abc123"

    def fetch_text(self, repo_url: str, *, path: str, ref: str) -> str:
        if "missing" in path:
            raise OSError("not found")
        return (
            "# Strategy research\n"
            "This project documents backtesting, dry-run, hyperopt, grid market making, funding arbitrage, "
            "risk controls, portfolio research, and technical signal strategy templates.\n"
        )


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
                    "license_policy": "distilled_research_only",
                    "asset_allowlist": [
                        {
                            "path": "README.md",
                            "asset_type": "documentation",
                            "extraction_tags": ["backtesting", "dry_run"],
                        }
                    ],
                    "strategy_extraction_targets": ["funding_carry", "trend_following"],
                    "license_notes": "research reference only",
                }
            ]
        ),
        encoding="utf-8",
    )
    library = OpenSourceStrategyLibrary(
        seed_manifest_path=seed,
        asset_root=tmp_path / "assets",
        remote_fetcher=FakeRemoteFetcher(),
    )

    first = library.import_sources(source_ids=["freqtrade"], refresh_assets=True, fetch_remote=True)
    second = library.import_sources(source_ids=["freqtrade"], refresh_assets=True, fetch_remote=True)

    assert len(first.imported) == 1
    assert len(second.imported) == 1
    assert first.imported[0].ingestion_status == "imported"
    assert first.imported[0].rag_asset_refs == second.imported[0].rag_asset_refs
    assert first.imported_assets
    assert first.imported_assets[0].sha256 == second.imported_assets[0].sha256
    assert (tmp_path / "assets" / "freqtrade" / "source_summary.md").exists()
    assert (tmp_path / "assets" / "freqtrade" / "asset_manifest.json").exists()


def test_open_source_extractor_creates_first_batch_strategy_ideas(tmp_path) -> None:
    library = OpenSourceStrategyLibrary(
        seed_manifest_path=Path("research_source/open_source_strategy_library/manifests/seed_sources.json"),
        asset_root=tmp_path / "assets",
        remote_fetcher=FakeRemoteFetcher(),
    )
    library.import_sources(source_ids=["hummingbot"], fetch_remote=True)
    manifest = library.get_source("hummingbot")
    assert manifest is not None

    ideas = OpenSourceStrategyExtractor().extract_ideas(manifest)
    buckets = {idea.intake_bucket for idea in ideas}
    titles = " ".join(idea.title.lower() for idea in ideas)

    assert "rule_candidate" in buckets
    assert "funding" in titles
    assert "grid" in titles or "market making" in titles
    assert all(idea.intake_metadata.get("asset_refs") for idea in ideas)


def test_open_source_import_records_failed_remote_assets(tmp_path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "source_id": "freqtrade",
                    "name": "Freqtrade",
                    "repo_url": "https://github.com/freqtrade/freqtrade",
                    "license": "GPL-3.0",
                    "license_policy": "distilled_research_only",
                    "project_role": "crypto_strategy_shapes",
                    "asset_categories": ["crypto_bot"],
                    "crypto_relevance": "high",
                    "asset_allowlist": [{"path": "missing.md", "asset_type": "documentation"}],
                    "strategy_extraction_targets": ["trend_following"],
                }
            ]
        ),
        encoding="utf-8",
    )
    library = OpenSourceStrategyLibrary(
        seed_manifest_path=seed,
        asset_root=tmp_path / "assets",
        remote_fetcher=FakeRemoteFetcher(),
    )

    result = library.import_sources(source_ids=["freqtrade"], fetch_remote=True)

    assert result.imported
    assert result.failed_assets
    assert result.failed_assets[0].ingestion_status == "failed"


def test_unknown_license_source_is_research_note_only(tmp_path) -> None:
    library = OpenSourceStrategyLibrary(
        seed_manifest_path=Path("research_source/open_source_strategy_library/manifests/seed_sources.json"),
        asset_root=tmp_path / "assets",
        remote_fetcher=FakeRemoteFetcher(),
    )
    library.import_sources(source_ids=["vibe_trading"], fetch_remote=True)
    manifest = library.get_source("vibe_trading")
    assert manifest is not None

    ideas = OpenSourceStrategyExtractor().extract_ideas(manifest)

    assert ideas
    assert {idea.intake_bucket for idea in ideas} == {"research_note_only"}
