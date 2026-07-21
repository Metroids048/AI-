"""Persist explicit Binance Simulation authorization for validated Paper runs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS, execution_scope_hash
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from shared.models import ConfigSnapshot


def arm_validated_directional_run(
    session: Session,
    *,
    symbols: list[str] | tuple[str, ...] = AUTO_SIMULATION_EXECUTION_SYMBOLS,
    verified_at: str | None = None,
) -> int:
    """Arm the OOS-validated directional run and its immutable config snapshot."""

    expected_symbols = list(symbols)
    verified = verified_at or datetime.now(UTC).isoformat()
    paper_repo = PaperRunRepository(session)
    config_repo = ConfigSnapshotRepository(session)
    strategy_repo = StrategyRepository(session)
    updated_count = 0

    for run in paper_repo.list_paper_runs():
        if run.execution_profile.get("auto_paper_runtime_key") != AUTO_PAPER_TECHNICAL_KEY:
            continue
        profile = {
            **run.execution_profile,
            "execution_mode": "binance_simulation_first",
            "mirror_to_gateway": True,
            "cost_gate_verified": True,
            "testnet_acceptance_verified_at": verified,
            "acceptance_symbols": expected_symbols,
            "acceptance_scope_hash": execution_scope_hash(expected_symbols),
        }
        updated = paper_repo.update_paper_run(run.paper_run_id or "", execution_profile=profile)
        if updated is None or updated.paper_run_id is None:
            continue

        active = config_repo.get_active(updated.paper_run_id)
        active_config = dict(active.config) if active is not None else {}
        strategy = strategy_repo.get_strategy(updated.strategy_id)
        strategy_rules = active_config.get("strategy_rules")
        if not isinstance(strategy_rules, dict):
            strategy_rules = strategy.rules.model_dump(mode="json") if strategy is not None else {}
        snapshot = ConfigSnapshot.create(
            paper_run_id=updated.paper_run_id,
            config={
                **active_config,
                "execution_profile": profile,
                "strategy_rules": strategy_rules,
                "risk_profile_id": active_config.get("risk_profile_id") or profile.get("risk_profile_id"),
            },
            created_by="testnet-acceptance",
            effective_cycle_id="NEXT_CYCLE" if active is not None else "TESTNET_ACCEPTANCE_BASELINE",
            previous_snapshot_id=active.config_snapshot_id if active is not None else None,
        )
        config_repo.create_snapshot(snapshot, base_config_hash=updated.active_config_hash)
        updated_count += 1
    return updated_count
