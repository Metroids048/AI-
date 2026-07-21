from __future__ import annotations

from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
from services.execution.testnet_authorization import arm_validated_directional_run
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository
from shared.models import ConfigSnapshot, PaperRun


def test_authorization_stages_next_cycle_snapshot_when_active_config_exists(db_session) -> None:
    run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id="directional-strategy",
            symbol_scope=["BTC/USDT", "ETH/USDT"],
            candidate_symbols=["BTC/USDT", "ETH/USDT"],
            paper_status="running",
            execution_profile={
                "auto_paper_runtime_key": AUTO_PAPER_TECHNICAL_KEY,
                "execution_mode": "paper_only",
                "mirror_to_gateway": False,
                "risk_profile_id": "paper-risk",
            },
        )
    )
    config_repo = ConfigSnapshotRepository(db_session)
    baseline = config_repo.create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=run.paper_run_id or "",
            config={
                "execution_profile": run.execution_profile,
                "strategy_rules": {"entry_rules": {"candidate_id": "trend_momentum_v1"}},
                "risk_profile_id": "paper-risk",
            },
            created_by="test",
            effective_cycle_id="baseline",
        ),
        base_config_hash=None,
    )

    assert arm_validated_directional_run(db_session) == 1

    active = config_repo.get_active(run.paper_run_id or "")
    pending = config_repo.get_pending(run.paper_run_id or "")
    assert active is not None
    assert active.config_hash == baseline.config_hash
    assert active.config["execution_profile"]["execution_mode"] == "paper_only"
    assert pending is not None
    assert pending.config["execution_profile"]["execution_mode"] == "binance_simulation_first"
    assert pending.config["execution_profile"]["mirror_to_gateway"] is True
    assert pending.config["strategy_rules"] == baseline.config["strategy_rules"]

    config_repo.activate_pending(run.paper_run_id or "", cycle_id="cycle-1")
    activated = config_repo.get_active(run.paper_run_id or "")
    assert activated is not None
    assert activated.config_hash == pending.config_hash
