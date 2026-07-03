"""Local importer for open-source strategy research manifests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from shared.models import ResearchSourceImportResult, StrategySourceManifest

from .asset_index import build_asset_index

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_SEED_MANIFEST = PACKAGE_ROOT / "manifests" / "seed_sources.json"
DEFAULT_ASSET_ROOT = PACKAGE_ROOT / "assets"


class OpenSourceStrategyLibrary:
    """Load source manifests and maintain local RAG-ready assets."""

    def __init__(
        self,
        *,
        seed_manifest_path: Path = DEFAULT_SEED_MANIFEST,
        asset_root: Path = DEFAULT_ASSET_ROOT,
    ) -> None:
        self.seed_manifest_path = seed_manifest_path
        self.asset_root = asset_root

    def load_seed_sources(self) -> list[StrategySourceManifest]:
        payload = json.loads(self.seed_manifest_path.read_text(encoding="utf-8"))
        return sorted(
            [StrategySourceManifest(**item) for item in payload],
            key=lambda item: (item.priority, item.source_id),
        )

    def list_sources(self) -> list[StrategySourceManifest]:
        return [self._hydrate_source_assets(source) for source in self.load_seed_sources()]

    def get_source(self, source_id: str) -> StrategySourceManifest | None:
        for source in self.list_sources():
            if source.source_id == source_id:
                return source
        return None

    def import_sources(
        self,
        *,
        source_ids: list[str] | None = None,
        refresh_assets: bool = True,
        fetch_remote: bool = False,
    ) -> ResearchSourceImportResult:
        requested = set(source_ids or [])
        imported: list[StrategySourceManifest] = []
        failed: list[StrategySourceManifest] = []
        for source in self.load_seed_sources():
            if requested and source.source_id not in requested:
                continue
            try:
                if refresh_assets:
                    self._write_local_summary_asset(source, fetch_remote=fetch_remote)
                imported.append(self._hydrate_source_assets(source).model_copy(update={"ingestion_status": "imported"}))
            except OSError as exc:
                failed.append(
                    source.model_copy(
                        update={
                            "ingestion_status": "failed",
                            "metadata": {"error": str(exc)},
                            "last_scanned_at": datetime.now(UTC),
                        }
                    )
                )
        return ResearchSourceImportResult(imported=imported, failed=failed)

    def _hydrate_source_assets(self, source: StrategySourceManifest) -> StrategySourceManifest:
        index = build_asset_index(source, asset_root=self.asset_root)
        refs = [item["path"] for item in index["assets"]]
        status = "imported" if refs else source.ingestion_status
        return source.model_copy(
            update={
                "ingestion_status": status,
                "rag_asset_refs": refs,
                "metadata": {**source.metadata, "rag_index": index},
                "last_scanned_at": datetime.now(UTC) if refs else source.last_scanned_at,
            }
        )

    def _write_local_summary_asset(self, source: StrategySourceManifest, *, fetch_remote: bool) -> Path:
        source_dir = self.asset_root / source.source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        summary_path = source_dir / "source_summary.md"
        remote_note = ""
        if fetch_remote:
            remote_note = (
                "\nRemote fetch requested; first implementation keeps runtime deterministic "
                "and records source metadata only.\n"
            )
        summary_path.write_text(
            "\n".join(
                [
                    f"# {source.name}",
                    "",
                    f"- Source ID: `{source.source_id}`",
                    f"- Repository: {source.repo_url}",
                    f"- License: {source.license}",
                    f"- Project role: {source.project_role}",
                    f"- Crypto relevance: {source.crypto_relevance}",
                    f"- Asset categories: {', '.join(source.asset_categories)}",
                    f"- License notes: {source.license_notes or 'not specified'}",
                    "",
                    "## Research Boundary",
                    "",
                    "This source is ingested as E-level research data. It can seed StrategyIdea records "
                    "and RAG context, but external runtime code must not bypass platform validation, "
                    "risk, review, or paper/live gates.",
                    "",
                    "## Source Notes",
                    "",
                    source.source_notes or "",
                    remote_note,
                ]
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return summary_path
