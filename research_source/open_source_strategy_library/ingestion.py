"""Local importer for open-source strategy research manifests."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from shared.models import ResearchSourceAsset, ResearchSourceImportResult, StrategySourceManifest

from .asset_index import build_asset_index, relative_repo_path

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_SEED_MANIFEST = PACKAGE_ROOT / "manifests" / "seed_sources.json"
DEFAULT_ASSET_ROOT = PACKAGE_ROOT / "assets"
ASSET_MANIFEST_FILENAME = "asset_manifest.json"


class GithubRemoteAssetFetcher:
    """Fetch a small allowlisted set of files from a GitHub repository."""

    def resolve_ref(self, repo_url: str) -> str:
        owner, repo = _parse_github_repo(repo_url)
        repo_payload = self._get_json(f"https://api.github.com/repos/{owner}/{repo}")
        default_branch = str(repo_payload.get("default_branch") or "main")
        try:
            commit_payload = self._get_json(f"https://api.github.com/repos/{owner}/{repo}/commits/{default_branch}")
            return str(commit_payload.get("sha") or default_branch)
        except OSError:
            return default_branch

    def fetch_text(self, repo_url: str, *, path: str, ref: str) -> str:
        owner, repo = _parse_github_repo(repo_url)
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path.lstrip('/')}"
        request = urllib.request.Request(url, headers={"User-Agent": "ai-quant-research-platform/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if ref and re.fullmatch(r"[0-9a-f]{40}", ref):
                repo_payload = self._get_json(f"https://api.github.com/repos/{owner}/{repo}")
                branch = str(repo_payload.get("default_branch") or "main")
                return self.fetch_text(repo_url, path=path, ref=branch)
            raise OSError(f"failed to fetch {path}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"failed to fetch {path}: {exc.reason}") from exc

    def _get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"User-Agent": "ai-quant-research-platform/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise OSError(f"failed to fetch {url}: {exc}") from exc
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise OSError(f"unexpected GitHub payload for {url}")
        return parsed


class OpenSourceStrategyLibrary:
    """Load source manifests and maintain local RAG-ready assets."""

    def __init__(
        self,
        *,
        seed_manifest_path: Path = DEFAULT_SEED_MANIFEST,
        asset_root: Path = DEFAULT_ASSET_ROOT,
        remote_fetcher: GithubRemoteAssetFetcher | None = None,
    ) -> None:
        self.seed_manifest_path = seed_manifest_path
        self.asset_root = asset_root
        self.remote_fetcher = remote_fetcher or GithubRemoteAssetFetcher()

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
        imported_assets: list[ResearchSourceAsset] = []
        failed_assets: list[ResearchSourceAsset] = []
        for source in self.load_seed_sources():
            if requested and source.source_id not in requested:
                continue
            try:
                if refresh_assets:
                    asset_result = self.refresh_source_assets(source, fetch_remote=fetch_remote)
                    imported_assets.extend(asset_result["imported_assets"])
                    failed_assets.extend(asset_result["failed_assets"])
                hydrated = self._hydrate_source_assets(source)
                imported.append(
                    hydrated.model_copy(
                        update={
                            "ingestion_status": "imported",
                            "metadata": {
                                **hydrated.metadata,
                                "imported_asset_count": len(imported_assets),
                                "failed_asset_count": len(failed_assets),
                            },
                        }
                    )
                )
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
        return ResearchSourceImportResult(
            imported=imported,
            failed=failed,
            imported_assets=imported_assets,
            failed_assets=failed_assets,
        )

    def list_assets(self, source_id: str) -> list[ResearchSourceAsset]:
        manifest_path = self.asset_root / source_id / ASSET_MANIFEST_FILENAME
        if not manifest_path.exists():
            return []
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return [ResearchSourceAsset(**item) for item in payload.get("assets", [])]

    def refresh_source_assets(
        self,
        source: StrategySourceManifest,
        *,
        fetch_remote: bool = True,
    ) -> dict[str, list[ResearchSourceAsset]]:
        imported_assets: list[ResearchSourceAsset] = []
        failed_assets: list[ResearchSourceAsset] = []
        summary_asset = self._write_local_summary_asset(source)
        imported_assets.append(summary_asset)
        if fetch_remote:
            origin_ref = self.remote_fetcher.resolve_ref(source.repo_url)
            for spec in source.asset_allowlist:
                if not isinstance(spec, dict) or not spec.get("path"):
                    continue
                remote_path = str(spec["path"])
                if _is_denied(remote_path, source.asset_denylist):
                    continue
                asset_type = str(spec.get("asset_type") or _infer_asset_type(remote_path))
                extraction_tags = [str(item) for item in spec.get("extraction_tags", [])]
                try:
                    raw_text = self.remote_fetcher.fetch_text(source.repo_url, path=remote_path, ref=origin_ref)
                    asset = self._write_distilled_remote_asset(
                        source,
                        remote_path=remote_path,
                        origin_ref=origin_ref,
                        asset_type=asset_type,
                        extraction_tags=extraction_tags,
                        raw_text=raw_text,
                    )
                    imported_assets.append(asset)
                except OSError as exc:
                    failed_assets.append(
                        _failed_asset(
                            source,
                            remote_path=remote_path,
                            origin_ref=origin_ref,
                            asset_type=asset_type,
                            extraction_tags=extraction_tags,
                            error=str(exc),
                            asset_root=self.asset_root,
                        )
                    )
        self._write_asset_manifest(source, imported_assets=imported_assets, failed_assets=failed_assets)
        return {"imported_assets": imported_assets, "failed_assets": failed_assets}

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

    def _write_local_summary_asset(self, source: StrategySourceManifest) -> ResearchSourceAsset:
        source_dir = self.asset_root / source.source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        summary_path = source_dir / "source_summary.md"
        body = (
            "\n".join(
                [
                    f"# {source.name}",
                    "",
                    f"- Source ID: `{source.source_id}`",
                    f"- Repository: {source.repo_url}",
                    f"- License: {source.license}",
                    f"- License policy: `{source.license_policy}`",
                    f"- Project role: {source.project_role}",
                    f"- Crypto relevance: {source.crypto_relevance}",
                    f"- Asset categories: {', '.join(source.asset_categories)}",
                    f"- Extraction targets: {', '.join(source.strategy_extraction_targets) or 'research_note'}",
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
                ]
            ).strip()
            + "\n"
        )
        summary_path.write_text(body, encoding="utf-8")
        return _asset_from_path(
            source,
            path=summary_path,
            asset_type="source_summary",
            origin_url=source.repo_url,
            origin_ref="local_manifest",
            extraction_tags=["source_boundary", *source.strategy_extraction_targets],
            summary=source.source_notes,
        )

    def _write_distilled_remote_asset(
        self,
        source: StrategySourceManifest,
        *,
        remote_path: str,
        origin_ref: str,
        asset_type: str,
        extraction_tags: list[str],
        raw_text: str,
    ) -> ResearchSourceAsset:
        source_dir = self.asset_root / source.source_id / _asset_subdir(asset_type)
        source_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(remote_path)
        output_path = source_dir / f"{filename}.md"
        distilled = _distill_remote_text(
            source=source,
            remote_path=remote_path,
            origin_ref=origin_ref,
            asset_type=asset_type,
            extraction_tags=extraction_tags,
            raw_text=raw_text,
        )
        output_path.write_text(distilled, encoding="utf-8")
        return _asset_from_path(
            source,
            path=output_path,
            asset_type=asset_type,
            origin_url=f"{source.repo_url}/blob/{origin_ref}/{remote_path}",
            origin_ref=origin_ref,
            extraction_tags=extraction_tags,
            summary=_summarize_text(raw_text),
        )

    def _write_asset_manifest(
        self,
        source: StrategySourceManifest,
        *,
        imported_assets: list[ResearchSourceAsset],
        failed_assets: list[ResearchSourceAsset],
    ) -> None:
        source_dir = self.asset_root / source.source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = source_dir / ASSET_MANIFEST_FILENAME
        merged = {item.local_path: item for item in self.list_assets(source.source_id)}
        for item in imported_assets:
            merged[item.local_path] = item
        payload = {
            "source_id": source.source_id,
            "repo_url": source.repo_url,
            "license": source.license,
            "license_policy": source.license_policy,
            "updated_at": datetime.now(UTC).isoformat(),
            "assets": [
                item.model_dump(mode="json") for item in sorted(merged.values(), key=lambda asset: asset.local_path)
            ],
            "failed_assets": [item.model_dump(mode="json") for item in failed_assets],
        }
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_github_repo(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc.lower() != "github.com" or len(parts) < 2:
        raise OSError(f"unsupported GitHub repository URL: {repo_url}")
    return parts[0], parts[1].removesuffix(".git")


def _asset_from_path(
    source: StrategySourceManifest,
    *,
    path: Path,
    asset_type: str,
    origin_url: str,
    origin_ref: str,
    extraction_tags: list[str],
    summary: str | None,
) -> ResearchSourceAsset:
    text = path.read_text(encoding="utf-8", errors="ignore")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ResearchSourceAsset(
        asset_id=f"{source.source_id}:{digest[:16]}",
        source_id=source.source_id,
        asset_type=asset_type,
        origin_url=origin_url,
        origin_ref=origin_ref,
        license=source.license,
        local_path=relative_repo_path(path),
        sha256=digest,
        bytes=len(text.encode("utf-8")),
        ingestion_status="imported",
        extraction_tags=extraction_tags,
        summary=summary,
        created_at=datetime.now(UTC),
    )


def _failed_asset(
    source: StrategySourceManifest,
    *,
    remote_path: str,
    origin_ref: str,
    asset_type: str,
    extraction_tags: list[str],
    error: str,
    asset_root: Path,
) -> ResearchSourceAsset:
    source_dir = asset_root / source.source_id
    local_path = relative_repo_path(source_dir / _asset_subdir(asset_type) / f"{_safe_filename(remote_path)}.md")
    digest = hashlib.sha256(f"{source.source_id}:{remote_path}:{error}".encode()).hexdigest()
    return ResearchSourceAsset(
        asset_id=f"{source.source_id}:failed:{digest[:12]}",
        source_id=source.source_id,
        asset_type=asset_type,
        origin_url=f"{source.repo_url}/blob/{origin_ref}/{remote_path}",
        origin_ref=origin_ref,
        license=source.license,
        local_path=local_path,
        sha256=digest,
        bytes=0,
        ingestion_status="failed",
        extraction_tags=extraction_tags,
        summary=error,
        created_at=datetime.now(UTC),
    )


def _is_denied(path: str, denylist: list[str]) -> bool:
    normalized = path.replace("\\", "/").lower()
    return any(pattern.lower() in normalized for pattern in denylist)


def _infer_asset_type(path: str) -> str:
    normalized = path.lower()
    if "strategy" in normalized or normalized.endswith(".py"):
        return "strategy_shape"
    if "doc" in normalized or normalized.endswith(".md") or normalized.endswith(".rst"):
        return "documentation"
    return "workflow_note"


def _asset_subdir(asset_type: str) -> str:
    if asset_type in {"strategy_shape", "strategy_template"}:
        return "strategy_shapes"
    if asset_type in {"workflow_note", "research_workflow", "architecture_note"}:
        return "workflow_notes"
    return "docs"


def _safe_filename(path: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "__", path.strip("/"))
    return normalized[:120].strip("._") or "asset"


def _distill_remote_text(
    *,
    source: StrategySourceManifest,
    remote_path: str,
    origin_ref: str,
    asset_type: str,
    extraction_tags: list[str],
    raw_text: str,
) -> str:
    summary = _summarize_text(raw_text)
    relevant_lines = _select_relevant_lines(raw_text)
    return "\n".join(
        [
            f"# {source.name} - {remote_path}",
            "",
            f"- Source ID: `{source.source_id}`",
            f"- Origin: `{source.repo_url}`",
            f"- Origin ref: `{origin_ref}`",
            f"- Remote path: `{remote_path}`",
            f"- License: `{source.license}`",
            f"- License policy: `{source.license_policy}`",
            f"- Asset type: `{asset_type}`",
            f"- Extraction tags: {', '.join(extraction_tags) or 'none'}",
            "",
            "## Distilled Summary",
            "",
            summary,
            "",
            "## RAG Notes",
            "",
            *[f"- {line}" for line in relevant_lines],
            "",
            "## Safety Boundary",
            "",
            "This is a distilled research asset. It is not imported as runtime trading code; "
            "any strategy derived from it must pass Strategy, Validation, Execution gatekeeper, and Review layers.",
            "",
        ]
    )


def _summarize_text(text: str) -> str:
    cleaned = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return cleaned[:800] or "No readable text extracted."


def _select_relevant_lines(text: str) -> list[str]:
    keywords = (
        "strategy",
        "backtest",
        "dry",
        "paper",
        "grid",
        "market making",
        "funding",
        "risk",
        "portfolio",
        "agent",
        "research",
        "data",
        "hyperopt",
        "optimization",
        "signal",
    )
    selected: list[str] = []
    for line in text.splitlines():
        cleaned = " ".join(line.strip().split())
        if not cleaned or len(cleaned) < 12:
            continue
        lower = cleaned.lower()
        if any(keyword in lower for keyword in keywords):
            selected.append(cleaned[:260])
        if len(selected) >= 16:
            break
    if selected:
        return selected
    return [_summarize_text(text)[:260]]
