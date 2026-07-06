"""Build local RAG asset metadata for open-source research sources."""

from __future__ import annotations

import json
from pathlib import Path

from shared.models import ResearchSourceAsset, StrategySourceManifest

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]


def relative_repo_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_asset_index(manifest: StrategySourceManifest, *, asset_root: Path) -> dict:
    """Return a compact RAG index entry for files under one source directory."""

    source_dir = asset_root / manifest.source_id
    assets: list[dict] = []
    if source_dir.exists():
        manifest_path = source_dir / "asset_manifest.json"
        manifest_assets: list[ResearchSourceAsset] = []
        if manifest_path.exists():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_assets = [ResearchSourceAsset(**item) for item in payload.get("assets", [])]
        manifest_by_path = {item.local_path: item for item in manifest_assets}
        for path in sorted(source_dir.rglob("*.md")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            local_path = relative_repo_path(path)
            manifest_asset = manifest_by_path.get(local_path)
            assets.append(
                {
                    "source_id": manifest.source_id,
                    "path": local_path,
                    "asset_id": manifest_asset.asset_id if manifest_asset is not None else None,
                    "asset_type": manifest_asset.asset_type if manifest_asset is not None else "local_markdown",
                    "origin_url": manifest_asset.origin_url if manifest_asset is not None else manifest.repo_url,
                    "origin_ref": manifest_asset.origin_ref if manifest_asset is not None else "local",
                    "license": manifest_asset.license if manifest_asset is not None else manifest.license,
                    "extraction_tags": manifest_asset.extraction_tags if manifest_asset is not None else [],
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
