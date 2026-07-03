"""Build local RAG asset metadata for open-source research sources."""

from __future__ import annotations

from pathlib import Path

from shared.models import StrategySourceManifest


def build_asset_index(manifest: StrategySourceManifest, *, asset_root: Path) -> dict:
    """Return a compact RAG index entry for files under one source directory."""

    source_dir = asset_root / manifest.source_id
    assets: list[dict] = []
    if source_dir.exists():
        for path in sorted(source_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assets.append(
                {
                    "source_id": manifest.source_id,
                    "path": path.as_posix(),
                    "bytes": len(text.encode("utf-8")),
                    "preview": " ".join(text.split())[:240],
                }
            )
    return {
        "source_id": manifest.source_id,
        "name": manifest.name,
        "repo_url": manifest.repo_url,
        "license": manifest.license,
        "asset_count": len(assets),
        "assets": assets,
    }
