from __future__ import annotations

from services.automated_trading.application.production_strategy import resolve_testnet_forward_authorization
from services.execution.runtime_config_migration import (
    stage_natural_testnet_sampling_snapshot,
    stage_promoted_runtime_config,
    stage_testnet_forward_runtime_config,
)
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from services.strategy_library.candidates.registry import get_candidate
from services.validation.forward_validation import ForwardDensityMetrics
from services.validation.strategy_promotion import PromotionResult
from shared.models import ConfigSnapshot, PaperRun, StrategyCreate


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


def test_manifest_sync_preserves_active_e003_execution_profile(db_session) -> None:
    strategy_repo = StrategyRepository(db_session)
    paper_repo = PaperRunRepository(db_session)
    config_repo = ConfigSnapshotRepository(db_session)
    strategy_key = "auto_paper_mature_templates_e003"
    strategy = strategy_repo.create_strategy(
        StrategyCreate(
            strategy_key=strategy_key,
            source="test",
            core_thesis="sync regression",
            rules={
                "entry_rules": {},
                "exit_rules": {},
                "stoploss_rules": {},
                "takeprofit_rules": {},
                "position_rules": {},
            },
        )
    )
    run = paper_repo.create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id or "",
            execution_profile={
                "auto_paper_runtime_key": strategy_key,
                "asset_risk_tiers": {
                    "core": {"tier": "core", "symbols": ["BTC/USDT"], "leverage": 50, "max_position_fraction": 2.5}
                },
            },
            paper_status="running",
        )
    )
    base_config = {
        "execution_profile": {
            **run.execution_profile,
            "asset_risk_tiers": {
                "symbol_btc": {
                    "tier": "symbol_btc",
                    "symbols": ["BTC/USDT"],
                    "leverage": 20,
                    "max_position_fraction": 0.4,
                    "risk_per_trade": 0.005,
                    "max_leverage": 20,
                    "max_margin_fraction": 0.02,
                }
            },
            "volatility_risk_tiers": {"low": {"tier": "low", "symbols": ["BTC/USDT"], "multiplier": 1.0}},
        },
        "strategy_rules": {"entry_rules": {}},
        "risk_profile_id": None,
    }
    base_snapshot = ConfigSnapshot.create(
        paper_run_id=run.paper_run_id or "",
        config=base_config,
        created_by="e003-test",
        effective_cycle_id="MIGRATION_BASELINE",
    )
    config_repo.create_snapshot(base_snapshot, base_config_hash=None)

    result = stage_promoted_runtime_config(
        db_session,
        strategy_key=strategy_key,
        promoted_rules={
            "entry_rules": {"candidate_id": "new"},
            "exit_rules": {},
            "stoploss_rules": {},
            "takeprofit_rules": {},
            "position_rules": {},
        },
        created_by="bootstrap-active-manifest-sync",
    )

    pending = config_repo.get_pending(run.paper_run_id or "")
    assert result.status == "staged"
    assert pending is not None
    assert (
        pending.config["execution_profile"]["asset_risk_tiers"] == base_config["execution_profile"]["asset_risk_tiers"]
    )
    assert (
        pending.config["execution_profile"]["volatility_risk_tiers"]
        == base_config["execution_profile"]["volatility_risk_tiers"]
    )


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


def _natural_sampling_run(db_session, *, with_active: bool = True):
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="natural_sampling_runtime",
            source="test",
            core_thesis="natural sampling",
            rules={
                "entry_rules": {},
                "exit_rules": {},
                "stoploss_rules": {},
                "takeprofit_rules": {},
                "position_rules": {},
            },
        )
    )
    run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id or "",
            execution_profile={
                "auto_paper_runtime_key": "natural_sampling_runtime",
                "risk_per_trade": 0.05,
                "operator_flag": "preserve-me",
                "simulation_sampling_fallback_enabled": False,
            },
            paper_status="running",
        )
    )
    if with_active:
        ConfigSnapshotRepository(db_session).create_snapshot(
            ConfigSnapshot.create(
                paper_run_id=run.paper_run_id or "",
                config={"execution_profile": dict(run.execution_profile), "strategy_rules": {"candidate": "stable"}},
                created_by="test",
                effective_cycle_id="MIGRATION_BASELINE",
            ),
            base_config_hash=None,
        )
    return run


def test_natural_sampling_stages_exactly_one_field_and_is_idempotent(db_session) -> None:
    run = _natural_sampling_run(db_session)
    repo = ConfigSnapshotRepository(db_session)

    first = stage_natural_testnet_sampling_snapshot(db_session, strategy_key="natural_sampling_runtime")
    pending = repo.get_pending(run.paper_run_id or "")
    assert first.status == "staged"
    assert pending is not None
    before = repo.get_active(run.paper_run_id or "")
    assert before is not None
    assert pending.config["strategy_rules"] == before.config["strategy_rules"]
    assert pending.config["execution_profile"]["operator_flag"] == "preserve-me"
    assert pending.config["execution_profile"]["simulation_sampling_fallback_enabled"] is True

    second = stage_natural_testnet_sampling_snapshot(db_session, strategy_key="natural_sampling_runtime")
    assert second.status == "pending_reused"
    assert second.config_snapshot_id == first.config_snapshot_id

    activated = repo.activate_pending(run.paper_run_id or "", cycle_id="natural-cycle")
    assert activated is not None
    third = stage_natural_testnet_sampling_snapshot(db_session, strategy_key="natural_sampling_runtime")
    assert third.status == "already_active"
    assert repo.get_pending(run.paper_run_id or "") is None


def test_natural_sampling_refuses_unrelated_pending_change(db_session) -> None:
    run = _natural_sampling_run(db_session)
    repo = ConfigSnapshotRepository(db_session)
    active = repo.get_active(run.paper_run_id or "")
    assert active is not None
    operator_pending = ConfigSnapshot.create(
        paper_run_id=run.paper_run_id or "",
        config={
            **active.config,
            "execution_profile": {**active.config["execution_profile"], "risk_per_trade": 0.01},
        },
        created_by="operator",
        effective_cycle_id="NEXT_CYCLE",
        previous_snapshot_id=active.config_snapshot_id,
    )
    repo.create_snapshot(operator_pending, base_config_hash=active.config_hash)

    try:
        stage_natural_testnet_sampling_snapshot(db_session, strategy_key="natural_sampling_runtime")
    except ValueError as exc:
        assert str(exc) == "CONFIG_PENDING_CONFLICT"
    else:
        raise AssertionError("expected CONFIG_PENDING_CONFLICT")


def test_persisted_forward_snapshot_activates_testnet_forward_without_self_hash(db_session) -> None:
    """P0: a real B snapshot activates and authorizes Testnet Forward."""
    candidate = get_candidate("trend_momentum_v2_enriched")
    strategy_key = "forward-runtime-binding-red"
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key=strategy_key,
            source="test",
            core_thesis="P0 circular snapshot binding reproduction",
            rules=candidate.get_config(),
        )
    )
    run = PaperRunRepository(db_session).create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id or "",
            symbol_scope=["BTC/USDT", "ETH/USDT"],
            candidate_symbols=["BTC/USDT", "ETH/USDT"],
            execution_profile={
                "auto_paper_runtime_key": strategy_key,
                "risk_profile_id": "test-risk-profile",
                "risk_per_trade": 0.05,
            },
            paper_status="running",
        )
    )
    active_result = stage_promoted_runtime_config(
        db_session,
        strategy_key=strategy_key,
        promoted_rules=candidate.get_config(),
    )
    config_repo = ConfigSnapshotRepository(db_session)
    active_a = config_repo.get_active(run.paper_run_id or "")
    assert active_a is not None
    assert active_a.config_hash == active_result.config_hash
    staged = stage_testnet_forward_runtime_config(
        db_session,
        strategy_key=strategy_key,
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        promoted_rules=candidate.get_config(),
        dataset_hash="d" * 64,
        validation_evidence_ref="artifacts/v001/evidence.json",
        validation_evidence_hash="e" * 64,
        eligible_execution_symbols=("BTC/USDT", "ETH/USDT"),
        density=ForwardDensityMetrics(
            eligible_closed_bars=1000,
            candidate_count=80,
            closed_trade_count=80,
            closed_trades_per_day=2.0,
            median_inter_trade_hours=8.0,
            p90_inter_trade_hours=24.0,
            estimated_days_to_30_closed_trades=30.0,
            passed=True,
        ),
        profitability_recovery=PromotionResult(eligible=True, failed_requirements=()),
    )
    assert staged.status == "staged"
    activated_b = config_repo.activate_pending(run.paper_run_id or "", cycle_id="p0-red")
    assert activated_b is not None
    reloaded_b = config_repo.get_active(run.paper_run_id or "")
    assert reloaded_b is not None

    authorization = resolve_testnet_forward_authorization(
        snapshot_config=reloaded_b.config,
        snapshot_hash=reloaded_b.config_hash,
        symbol="BTC/USDT",
        execution_mode="BINANCE_TESTNET",
    )

    assert authorization.authorized is True
    assert authorization.reason == "TESTNET_FORWARD_AUTHORIZED"
    assert reloaded_b.config["forward_validation"]["schema_version"] == "forward-validation-handoff-v2"
    assert reloaded_b.config["forward_validation"]["runtime_config_binding_hash"] != reloaded_b.config_hash
