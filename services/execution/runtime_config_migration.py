"""Explicit migration from legacy mutable strategy rows to runtime config snapshots."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from services.automated_trading.application.strategy_package_identity import strategy_package_identity
from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from services.validation.forward_validation import (
    ForwardDensityMetrics,
    build_forward_validation_handoff,
    forward_runtime_binding_hash,
)
from services.validation.strategy_promotion import PromotionResult
from shared.models import ConfigSnapshot, StrategyRules


@dataclass(frozen=True, slots=True)
class RuntimeConfigMigrationResult:
    paper_run_id: str
    config_snapshot_id: str
    config_hash: str
    status: str


def _runtime_config_base(
    *,
    paper_run,
    active: ConfigSnapshot | None,
    promoted_rules: dict[str, Any],
    canonical_strategy_manifest: dict[str, str] | None,
) -> dict[str, Any]:
    active_profile = active.config.get("execution_profile") if active is not None else None
    execution_profile = (
        {**paper_run.execution_profile, **active_profile}
        if isinstance(active_profile, dict)
        else dict(paper_run.execution_profile)
    )
    config: dict[str, Any] = {
        "execution_profile": deepcopy(execution_profile),
        "strategy_rules": deepcopy(promoted_rules),
        "risk_profile_id": execution_profile.get("risk_profile_id"),
    }
    active_manifest = active.config.get("canonical_strategy_manifest") if active is not None else None
    manifest = canonical_strategy_manifest if canonical_strategy_manifest is not None else active_manifest
    if isinstance(manifest, dict):
        config["canonical_strategy_manifest"] = deepcopy(manifest)
    return config


def _runtime_config_context(session: Session, *, strategy_key: str):
    strategy = next(
        (item for item in StrategyRepository(session).list_strategies() if item.strategy_key == strategy_key),
        None,
    )
    if strategy is None:
        raise ValueError(f"strategy not found: {strategy_key}")
    paper_run = next(
        (
            item
            for item in PaperRunRepository(session).list_paper_runs()
            if item.strategy_id == strategy.strategy_id
            and item.execution_profile.get("auto_paper_runtime_key") == strategy_key
        ),
        None,
    )
    if paper_run is None or paper_run.paper_run_id is None:
        raise ValueError(f"paper run not found for strategy: {strategy_key}")
    config_repo = ConfigSnapshotRepository(session)
    return (
        paper_run,
        config_repo,
        config_repo.get_active(paper_run.paper_run_id),
        config_repo.get_pending(paper_run.paper_run_id),
    )


def stage_promoted_runtime_config(
    session: Session,
    *,
    strategy_key: str,
    promoted_rules: dict[str, Any],
    canonical_strategy_manifest: dict[str, str] | None = None,
    created_by: str = "runtime-config-migration",
) -> RuntimeConfigMigrationResult:
    """Activate or stage promoted rules without mutating the legacy Strategy row.

    A run without a snapshot receives an immediately active baseline.  A run
    that already has an active snapshot receives a NEXT_CYCLE pending snapshot,
    preserving the repository's optimistic-concurrency contract.
    """

    paper_run, config_repo, active, pending = _runtime_config_context(session, strategy_key=strategy_key)
    if pending is not None:
        # Another update is already queued. Bootstrap may refresh derived run
        # metadata, but it must not replace an operator's NEXT_CYCLE contract.
        return RuntimeConfigMigrationResult(
            paper_run_id=paper_run.paper_run_id,
            config_snapshot_id=pending.config_snapshot_id or "",
            config_hash=pending.config_hash,
            status="pending_preserved",
        )
    config = _runtime_config_base(
        paper_run=paper_run,
        active=active,
        promoted_rules=promoted_rules,
        canonical_strategy_manifest=canonical_strategy_manifest,
    )
    snapshot = ConfigSnapshot.create(
        paper_run_id=paper_run.paper_run_id,
        config=config,
        created_by=created_by,
        effective_cycle_id="NEXT_CYCLE" if active is not None else "MIGRATION_BASELINE",
        previous_snapshot_id=active.config_snapshot_id if active is not None else None,
    )
    if active is not None and active.config_hash == snapshot.config_hash:
        return RuntimeConfigMigrationResult(
            paper_run_id=paper_run.paper_run_id,
            config_snapshot_id=active.config_snapshot_id or "",
            config_hash=active.config_hash,
            status="already_active",
        )

    created = config_repo.create_snapshot(snapshot, base_config_hash=paper_run.active_config_hash)
    return RuntimeConfigMigrationResult(
        paper_run_id=paper_run.paper_run_id,
        config_snapshot_id=created.config_snapshot_id or "",
        config_hash=created.config_hash,
        status="staged" if active is not None else "activated",
    )


def stage_testnet_forward_runtime_config(
    session: Session,
    *,
    strategy_key: str,
    candidate_id: str,
    candidate_version: str,
    promoted_rules: dict[str, Any],
    dataset_hash: str,
    validation_evidence_ref: str,
    validation_evidence_hash: str,
    eligible_execution_symbols: tuple[str, ...],
    density: ForwardDensityMetrics,
    profitability_recovery: PromotionResult,
    canonical_strategy_manifest: dict[str, str] | None = None,
    created_by: str = "stage-testnet-forward-runtime-config",
) -> RuntimeConfigMigrationResult:
    """Atomically stage the only valid Testnet Forward ConfigSnapshot contract."""
    paper_run, config_repo, active, pending = _runtime_config_context(session, strategy_key=strategy_key)
    if active is None:
        raise ValueError("TESTNET_FORWARD_REQUIRES_ACTIVE_CONFIG_SNAPSHOT")
    if pending is not None:
        raise ValueError("TESTNET_FORWARD_PENDING_CONFIG_EXISTS")

    rules = StrategyRules(**promoted_rules)
    rules_hash = strategy_rules_hash(rules)
    identity = strategy_package_identity(
        strategy_id=candidate_id,
        strategy_version=candidate_version,
        rules_hash=rules_hash,
    )
    manifest_binding = canonical_strategy_manifest or identity.snapshot_binding()
    if manifest_binding != identity.snapshot_binding():
        raise ValueError("FORWARD_VALIDATION_RUNTIME_CONFIG_BINDING_MISMATCH")
    base_config = _runtime_config_base(
        paper_run=paper_run,
        active=active,
        promoted_rules=promoted_rules,
        canonical_strategy_manifest=manifest_binding,
    )
    binding_hash = forward_runtime_binding_hash(base_config)
    handoff = build_forward_validation_handoff(
        candidate_id=candidate_id,
        candidate_version=candidate_version,
        strategy_rules=promoted_rules,
        dataset_hash=dataset_hash,
        validation_evidence_ref=validation_evidence_ref,
        validation_evidence_hash=validation_evidence_hash,
        eligible_execution_symbols=eligible_execution_symbols,
        density=density,
        profitability_recovery=profitability_recovery,
        runtime_config=base_config,
        frozen_at=active.created_at or datetime.now(UTC),
    )
    if handoff.runtime_config_binding_hash != binding_hash:
        raise ValueError("FORWARD_VALIDATION_RUNTIME_CONFIG_BINDING_MISMATCH")
    config = {**base_config, "forward_validation": handoff.model_dump(mode="json")}
    snapshot = ConfigSnapshot.create(
        paper_run_id=paper_run.paper_run_id,
        config=config,
        created_by=created_by,
        effective_cycle_id="NEXT_CYCLE",
        previous_snapshot_id=active.config_snapshot_id,
    )
    created = config_repo.create_snapshot(snapshot, base_config_hash=paper_run.active_config_hash)
    return RuntimeConfigMigrationResult(
        paper_run_id=paper_run.paper_run_id,
        config_snapshot_id=created.config_snapshot_id or "",
        config_hash=created.config_hash,
        status="staged",
    )
