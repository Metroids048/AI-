from __future__ import annotations

from services.strategy_library.repository import (
    IngestionRepository,
    PaperRunRepository,
    ReviewRepository,
    StrategyRepository,
    ValidationRepository,
)
from shared.models import (
    BacktestEngine,
    BacktestReport,
    BacktestRun,
    FailureRecord,
    GateDecision,
    IngestionJob,
    PaperRun,
    StrategyCreate,
    StrategyIdea,
    StrategyRules,
    StrategyUpdate,
    StrategyVersion,
)


def test_strategy_repository_lifecycle(db_session) -> None:
    repo = StrategyRepository(db_session)

    idea = repo.create_idea(
        StrategyIdea(
            title="Funding carry from note intake",
            source="manual_note",
            hypothesis_summary="Positive funding windows can support short perp / long hedge carry.",
            symbol_scope=["BTC/USDT", "ETH/USDT"],
            intake_metadata={"raw_expression": "funding_rate", "behavior_signature": "carry"},
        )
    )
    assert idea.idea_id is not None
    assert idea.intake_metadata["behavior_signature"] == "carry"

    draft = repo.promote_idea_to_draft(idea.idea_id)
    assert draft is not None
    assert draft.idea_id == idea.idea_id

    materialized = repo.materialize_strategy_from_draft(draft.draft_id)
    assert materialized is not None
    assert materialized.source == "manual_note"
    assert materialized.symbol_scope == ["BTC/USDT", "ETH/USDT"]

    updated = repo.update_strategy(
        materialized.strategy_id,
        StrategyUpdate(
            core_thesis=draft.core_thesis,
            risk_level=draft.risk_level,
            rules=StrategyRules(
                entry_rules={"funding_threshold_bps": 5},
                exit_rules={"hold_to_settlement": True},
                stoploss_rules={"basis_bps": 20},
                takeprofit_rules={"close_after_windows": 1},
                position_rules={"hedge_ratio": 1.0},
            ),
        ),
    )
    assert updated is not None
    assert updated.rules.entry_rules["funding_threshold_bps"] == 5


def test_review_repository_records_idea_level_failure(db_session) -> None:
    strategy_repo = StrategyRepository(db_session)
    review_repo = ReviewRepository(db_session)
    idea = strategy_repo.create_idea(
        StrategyIdea(
            title="Unsupported alpha",
            source="worldquant_local_alpha",
            hypothesis_summary="stock field needs manual crypto port",
            intake_bucket="subjective_to_drop",
            intake_metadata={"raw_expression": "ts_delta(capex_to_total_assets,252)"},
        )
    )
    assert idea.idea_id is not None

    failure = review_repo.create_failure(
        FailureRecord(
            idea_id=idea.idea_id,
            origin_run_type="research_intake",
            origin_run_id=idea.idea_id,
            failure_type="alpha_evaluator_reject",
            failure_summary="Unsupported input capex_to_total_assets",
        )
    )

    failures = review_repo.list_failures(idea_id=idea.idea_id, failure_type="alpha_evaluator_reject")
    assert failure.failure_record_id is not None
    assert len(failures) == 1
    assert failures[0].strategy_id is None
    assert failures[0].idea_id == idea.idea_id


def test_run_repositories_store_backtest_ingestion_and_paper(db_session) -> None:
    strategy_repo = StrategyRepository(db_session)
    validation_repo = ValidationRepository(db_session)
    ingestion_repo = IngestionRepository(db_session)
    paper_repo = PaperRunRepository(db_session)

    strategy = strategy_repo.create_strategy(
        StrategyCreate(
            strategy_key="Carry_Strategy_v1",
            source="manual",
            core_thesis="carry",
            rules=StrategyRules(
                entry_rules={"funding_threshold_bps": 5},
                exit_rules={"hold_to_settlement": True},
                stoploss_rules={"basis_bps": 20},
                takeprofit_rules={"close_after_windows": 1},
                position_rules={"hedge_ratio": 1.0},
            ),
        )
    )

    version = strategy_repo.create_version(
        StrategyVersion(
            strategy_id=strategy.strategy_id,
            version_label="v1",
            change_summary="initial carry slice",
        )
    )
    assert version.version_id is not None

    backtest = validation_repo.create_backtest_run(
        BacktestRun(
            strategy_id=strategy.strategy_id,
            version_id=version.version_id,
            execution_engine=BacktestEngine.FREQTRADE,
            sample_split_plan={"train": "2024Q1", "oos": "2024Q2"},
            validation_methodology={"lane": "carry_research"},
            cost_model_ref="platform-side spot hedge reconciliation",
            stress_test_scenarios=["funding_flip", "missing_settlement"],
            metrics_summary=BacktestReport(
                strategy_id=strategy.strategy_id,
                engine=BacktestEngine.FREQTRADE,
                sharpe=1.5,
                profit_factor=1.4,
                max_drawdown=0.12,
                win_rate=0.58,
                expectancy=0.11,
                total_cost_bps=14,
            ),
            eligibility_result=GateDecision(
                strategy_id=strategy.strategy_id,
                passed=True,
                decision_status="conditional",
                reason="deflated sharpe pending",
            ),
        )
    )
    assert backtest.backtest_run_id is not None
    assert backtest.eligibility_result is not None
    assert backtest.eligibility_result.decision_status == "conditional"

    ingestion_job = ingestion_repo.create_job(
        IngestionJob(
            source_family="A",
            source_name="binance",
            job_type="top20_historical_backfill",
            schedule_mode="manual",
            input_window={"symbols": ["BTC/USDT", "ETH/USDT"]},
            target_symbols=["BTC/USDT", "ETH/USDT"],
        )
    )
    assert ingestion_job.ingestion_job_id is not None
    assert ingestion_job.target_symbols == ["BTC/USDT", "ETH/USDT"]

    paper_run = paper_repo.create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id,
            version_id=version.version_id,
            symbol_scope=["BTC/USDT", "ETH/USDT"],
            candidate_symbols=["BTC/USDT", "ETH/USDT"],
            selection_basis="binance_top20_quote_volume",
            gate_decision_ref=backtest.backtest_run_id,
        )
    )
    assert paper_run.paper_run_id is not None
    assert paper_run.candidate_symbols == ["BTC/USDT", "ETH/USDT"]
