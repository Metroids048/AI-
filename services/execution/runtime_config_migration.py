"""Explicit migration from legacy mutable strategy rows to runtime config snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from shared.models import ConfigSnapshot


@dataclass(frozen=True, slots=True)
class RuntimeConfigMigrationResult:
    paper_run_id: str
    config_snapshot_id: str
    config_hash: str
    status: str


def stage_promoted_runtime_config(
    session: Session,
    *,
    strategy_key: str,
    promoted_rules: dict[str, Any],
    created_by: str = "runtime-config-migration",
) -> RuntimeConfigMigrationResult:
    """Activate or stage promoted rules without mutating the legacy Strategy row.

    A run without a snapshot receives an immediately active baseline.  A run
    that already has an active snapshot receives a NEXT_CYCLE pending snapshot,
    preserving the repository's optimistic-concurrency contract.
    """

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
    active = config_repo.get_active(paper_run.paper_run_id)
    pending = config_repo.get_pending(paper_run.paper_run_id)
    if pending is not None:
        # Another update is already queued. Bootstrap may refresh derived run
        # metadata, but it must not replace an operator's NEXT_CYCLE contract.
        return RuntimeConfigMigrationResult(
            paper_run_id=paper_run.paper_run_id,
            config_snapshot_id=pending.config_snapshot_id or "",
            config_hash=pending.config_hash,
            status="pending_preserved",
        )
    config = {
        "execution_profile": paper_run.execution_profile,
        "strategy_rules": promoted_rules,
        "risk_profile_id": paper_run.execution_profile.get("risk_profile_id"),
    }
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
