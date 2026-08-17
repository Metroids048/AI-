"""E-003 volatility-aware symbol risk configuration staging for Testnet directional."""

from __future__ import annotations

import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.database import get_session_factory
from services.execution.risk_tiers import symbol_risk_base_tiers
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository
from shared.models import ConfigSnapshot


def stage_e003_volatility_aware_risk_config() -> dict[str, str]:
    """Create a new NEXT_CYCLE pending snapshot with E-003 symbol base ceilings.

    Does not activate or replace existing configurations. Returns metadata for
    verification: paper_run_id, previous active hash, new pending snapshot_id.
    """
    with get_session_factory()() as session:
        paper_repo = PaperRunRepository(session)
        config_repo = ConfigSnapshotRepository(session)

        running = [
            run
            for run in paper_repo.list_paper_runs()
            if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
        ]
        if not running:
            raise RuntimeError("no running directional PaperRun found")
        if len(running) > 1:
            raise RuntimeError(f"ambiguous running directional runs: {[r.paper_run_id for r in running]}")

        run = running[0]
        active = config_repo.get_active(run.paper_run_id or "")
        if active is None:
            raise RuntimeError("running directional PaperRun has no active config snapshot")

        profile = dict(active.config.get("execution_profile") or run.execution_profile)
        # E-003: replace only asset_risk_tiers with per-symbol base ceilings.
        # Leave volatility_risk_tiers empty so the first Weekly ATR sweep can populate.
        profile["asset_risk_tiers"] = symbol_risk_base_tiers()
        profile.setdefault("volatility_risk_tiers", {})

        new_config = {**active.config, "execution_profile": profile}
        snapshot = ConfigSnapshot.create(
            paper_run_id=run.paper_run_id or "",
            config=new_config,
            created_by="e003-volatility-aware-risk-remediation",
            effective_cycle_id="NEXT_CYCLE",
            previous_snapshot_id=active.config_snapshot_id,
        )

        created = config_repo.create_snapshot(snapshot, base_config_hash=run.active_config_hash)
        return {
            "status": "PENDING_STAGED",
            "paper_run_id": run.paper_run_id or "",
            "previous_active_snapshot_id": active.config_snapshot_id or "",
            "previous_active_hash": run.active_config_hash or "",
            "new_pending_snapshot_id": created.config_snapshot_id or "",
            "new_pending_hash": created.config_hash,
            "effective_cycle_id": "NEXT_CYCLE",
            "created_by": "e003-volatility-aware-risk-remediation",
        }


if __name__ == "__main__":
    result = stage_e003_volatility_aware_risk_config()
    print(json.dumps(result, ensure_ascii=False, indent=2))
