from __future__ import annotations

from services.execution.runtime_config_migration import stage_promoted_runtime_config
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from shared.models import PaperRun, StrategyCreate


def test_stage_promoted_runtime_config_activates_snapshot_without_mutating_strategy_row(db_session) -> None:
    strategy_repo = StrategyRepository(db_session)
    paper_repo = PaperRunRepository(db_session)
    strategy = strategy_repo.create_strategy(
        StrategyCreate(
            strategy_key="auto_paper_mature_templates",
            source="test",
            core_thesis="legacy persisted rules",
            rules={
                "entry_rules": {"candidate_id": "operator_heuristic_v1"},
                "exit_rules": {},
                "stoploss_rules": {"fixed_bps": 250},
                "takeprofit_rules": {"risk_reward": 2.0},
                "position_rules": {"risk_per_trade": 0.05},
            },
        )
    )
    run = paper_repo.create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id or "",
            symbol_scope=["BTC/USDT", "ETH/USDT"],
            candidate_symbols=["BTC/USDT", "ETH/USDT"],
            execution_profile={
                "auto_paper_runtime_key": "auto_paper_mature_templates",
                "risk_profile_id": "medium_binance_top20",
                "risk_per_trade": 0.05,
            },
            paper_status="running",
        )
    )
    promoted_rules = {
        "entry_rules": {"candidate_id": "trend_momentum_v1"},
        "exit_rules": {},
        "stoploss_rules": {"fixed_bps": 250},
        "takeprofit_rules": {"risk_reward": 2.0},
        "position_rules": {"risk_per_trade": 0.05},
    }

    result = stage_promoted_runtime_config(
        db_session,
        strategy_key="auto_paper_mature_templates",
        promoted_rules=promoted_rules,
    )

    active = ConfigSnapshotRepository(db_session).get_active(run.paper_run_id or "")
    assert result.status == "activated"
    assert active is not None
    assert active.config["strategy_rules"]["entry_rules"]["candidate_id"] == "trend_momentum_v1"
    assert active.config["execution_profile"]["risk_per_trade"] == 0.05
    persisted = strategy_repo.get_strategy(strategy.strategy_id or "")
    assert persisted is not None
    assert persisted.rules.entry_rules["candidate_id"] == "operator_heuristic_v1"


def test_stage_promoted_runtime_config_is_idempotent_for_same_hash(db_session) -> None:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="auto_paper_mature_templates",
            source="test",
            core_thesis="legacy",
            rules={
                "entry_rules": {},
                "exit_rules": {},
                "stoploss_rules": {},
                "takeprofit_rules": {},
                "position_rules": {},
            },
        )
    )
    PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id or "",
            execution_profile={"auto_paper_runtime_key": "auto_paper_mature_templates"},
            paper_status="running",
        )
    )
    promoted_rules = {
        "entry_rules": {"candidate_id": "trend_momentum_v1"},
        "exit_rules": {},
        "stoploss_rules": {},
        "takeprofit_rules": {},
        "position_rules": {},
    }

    first = stage_promoted_runtime_config(
        db_session,
        strategy_key="auto_paper_mature_templates",
        promoted_rules=promoted_rules,
    )
    second = stage_promoted_runtime_config(
        db_session,
        strategy_key="auto_paper_mature_templates",
        promoted_rules=promoted_rules,
    )

    assert first.config_hash == second.config_hash
    assert second.status == "already_active"
    assert len(ConfigSnapshotRepository(db_session).list_snapshots(first.paper_run_id)) == 1
