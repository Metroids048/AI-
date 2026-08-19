"""Gate 3.5 strategy package identity is deterministic and scope-limited."""

from __future__ import annotations

import json
from pathlib import Path

from services.automated_trading.application.strategy_package_identity import (
    strategy_package_identity,
    strategy_source_files,
)


def _write_strategy_sources(root: Path, *, candidate_id: str) -> None:
    for relative_path in strategy_source_files(candidate_id):
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"source:{relative_path}\n", encoding="utf-8")


def test_dependency_inventory_covers_strategy_path_but_not_runtime_or_presentation() -> None:
    sources = strategy_source_files("trend_momentum_v2_enriched")

    assert "services/automated_trading/application/production_strategy.py" in sources
    assert "services/execution/decision_pipeline.py" in sources
    assert "services/execution/signal_edge_stats.py" in sources
    assert "services/strategy_library/candidates/registry.py" in sources
    assert "services/strategy_library/technical/indicators.py" in sources
    assert "services/automated_trading/application/reconciliation_service.py" not in sources
    assert "frontend/admin/src/RuntimeTruthPanel.jsx" not in sources
    assert "README.md" not in sources


def test_packaged_manifest_matches_recomputed_strategy_package_identity() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = json.loads(
        (repo_root / "docs/evidence/active-manifests/auto_paper_mature_templates.json").read_text(encoding="utf-8")
    )

    observed = strategy_package_identity(
        strategy_id=manifest["strategy_id"],
        strategy_version=manifest["strategy_version"],
        rules_hash=manifest["rules_hash"],
        source_root=repo_root,
    )

    assert observed.strategy_code_hash == manifest["strategy_code_hash"]
    assert observed.strategy_package_hash == manifest["strategy_package_hash"]


def test_bootstrap_manifest_binding_is_the_recomputed_package_identity() -> None:
    from services.execution.bootstrap import resolve_auto_paper_manifest_binding

    binding = resolve_auto_paper_manifest_binding()

    assert binding is not None
    assert binding["strategy_code_hash"]
    assert binding["strategy_package_hash"]


def test_identity_ignores_frontend_docs_and_reconciliation_only_files(tmp_path: Path) -> None:
    candidate_id = "trend_momentum_v2_enriched"
    _write_strategy_sources(tmp_path, candidate_id=candidate_id)
    baseline = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version="2.0.0",
        rules_hash="a" * 64,
        source_root=tmp_path,
    )

    for relative_path in (
        "frontend/admin/src/RuntimeTruthPanel.jsx",
        "README.md",
        "services/automated_trading/application/reconciliation_service.py",
    ):
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("unrelated change\n", encoding="utf-8")

    observed = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version="2.0.0",
        rules_hash="a" * 64,
        source_root=tmp_path,
    )

    assert observed == baseline


def test_identity_changes_when_canonical_signal_source_changes(tmp_path: Path) -> None:
    candidate_id = "trend_momentum_v2_enriched"
    _write_strategy_sources(tmp_path, candidate_id=candidate_id)
    baseline = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version="2.0.0",
        rules_hash="a" * 64,
        source_root=tmp_path,
    )
    signal_source = tmp_path / "services/strategy_library/technical/indicators.py"
    signal_source.write_text("changed signal implementation\n", encoding="utf-8")

    observed = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version="2.0.0",
        rules_hash="a" * 64,
        source_root=tmp_path,
    )

    assert observed.strategy_code_hash != baseline.strategy_code_hash
    assert observed.strategy_package_hash != baseline.strategy_package_hash


def test_package_hash_changes_for_rules_or_version_without_code_change(tmp_path: Path) -> None:
    candidate_id = "trend_momentum_v2_enriched"
    _write_strategy_sources(tmp_path, candidate_id=candidate_id)
    baseline = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version="2.0.0",
        rules_hash="a" * 64,
        source_root=tmp_path,
    )
    changed_rules = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version="2.0.0",
        rules_hash="b" * 64,
        source_root=tmp_path,
    )
    changed_version = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version="2.0.1",
        rules_hash="a" * 64,
        source_root=tmp_path,
    )

    assert changed_rules.strategy_code_hash == baseline.strategy_code_hash
    assert changed_rules.strategy_package_hash != baseline.strategy_package_hash
    assert changed_version.strategy_package_hash != baseline.strategy_package_hash
