from __future__ import annotations

from pathlib import Path

from services.execution.bootstrap import (
    AUTO_PAPER_RUNTIME_KEY,
    AUTO_PAPER_TECHNICAL_KEY,
    OPERATOR_EXPERIENCE_STRATEGY_KEY,
    bootstrap_auto_trading_paper_run,
    bootstrap_auto_trading_technical_paper_run,
    bootstrap_operator_experience_strategy,
    bootstrap_paper_testnet_mirror,
    bootstrap_pause_legacy_paper_runs,
    default_mirror_to_gateway,
)
from services.execution.paper import PaperOrchestrationService
from shared.config import settings
from shared.models import PaperRun, RunStatus


def test_default_mirror_to_gateway_uses_safe_binance_simulation_boundary(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    assert default_mirror_to_gateway() is False

    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_auto_execute", False)
    assert default_mirror_to_gateway() is False

    monkeypatch.setattr(settings, "binance_auto_execute", True)
    assert default_mirror_to_gateway() is True

    monkeypatch.setattr(settings, "live_trading_enabled", True)
    assert default_mirror_to_gateway() is False


def test_prepare_run_keeps_mirror_disabled_when_credentials_present(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    prepared = PaperOrchestrationService().prepare_run(
        PaperRun(strategy_id="s1", symbol_scope=["BTC/USDT"], execution_profile={})
    )
    assert prepared.execution_profile.get("mirror_to_gateway") is False


def test_bootstrap_paper_testnet_mirror_does_not_update_running_runs(db_session, monkeypatch) -> None:
    from services.strategy_library import PaperRunRepository

    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    repo = PaperRunRepository(db_session)
    created = repo.create_paper_run(
        PaperRun(
            strategy_id="s1",
            symbol_scope=["BTC/USDT"],
            paper_status="running",
            execution_profile={"mirror_to_gateway": False},
        )
    )
    assert bootstrap_paper_testnet_mirror() == 0
    updated = repo.get_paper_run(created.paper_run_id or "")
    assert updated is not None
    assert updated.execution_profile.get("mirror_to_gateway") is False


def test_bootstrap_pauses_retired_technical_auto_run(db_session) -> None:
    from services.strategy_library import PaperRunRepository

    repo = PaperRunRepository(db_session)
    retired = repo.create_paper_run(
        PaperRun(
            strategy_id="retired-strategy",
            paper_status="running",
            execution_profile={"auto_paper_runtime_key": "auto_paper_btc_technical"},
        )
    )

    assert bootstrap_pause_legacy_paper_runs() == 1
    assert repo.get_paper_run(retired.paper_run_id or "").paper_status == "paused"


def test_bootstrap_creates_carry_and_directional_runs(db_session, monkeypatch) -> None:
    from services.strategy_library import PaperRunRepository, StrategyRepository

    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(settings, "binance_auto_execute", True)

    carry_id = bootstrap_auto_trading_paper_run()
    technical_id = bootstrap_auto_trading_technical_paper_run()

    assert carry_id is not None
    assert technical_id is not None
    assert carry_id != technical_id

    paper_repo = PaperRunRepository(db_session)
    carry_run = paper_repo.get_paper_run(carry_id)
    technical_run = paper_repo.get_paper_run(technical_id)
    assert carry_run is not None
    assert technical_run is not None
    assert carry_run.execution_profile.get("strategy_lane") == "carry"
    assert technical_run.execution_profile.get("strategy_lane") == "directional"
    assert carry_run.execution_profile.get("execution_mode") == "binance_simulation_first"
    assert technical_run.execution_profile.get("execution_mode") == "binance_simulation_first"
    assert carry_run.execution_profile.get("mirror_to_gateway") is True
    assert technical_run.execution_profile.get("mirror_to_gateway") is True
    assert carry_run.selection_basis == "fixed_operator_top20"
    assert len(carry_run.candidate_symbols) == 20

    strategy_repo = StrategyRepository(db_session)
    carry_strategy = next(
        item for item in strategy_repo.list_strategies() if item.strategy_key == AUTO_PAPER_RUNTIME_KEY
    )
    technical_strategy = next(
        item for item in strategy_repo.list_strategies() if item.strategy_key == AUTO_PAPER_TECHNICAL_KEY
    )
    assert "funding_threshold_bps" in carry_strategy.rules.entry_rules
    assert "technical_pipeline" in technical_strategy.rules.entry_rules
    assert "funding_threshold_bps" not in technical_strategy.rules.entry_rules


def test_bootstrap_operator_experience_strategy_uses_valid_disabled_research_state(db_session) -> None:
    from services.strategy_library import StrategyRepository

    strategy_id = bootstrap_operator_experience_strategy()

    strategy = StrategyRepository(db_session).get_strategy(strategy_id or "")
    assert strategy is not None
    assert strategy.strategy_key == OPERATOR_EXPERIENCE_STRATEGY_KEY
    assert strategy.paper_status is RunStatus.NOT_STARTED


def test_local_api_start_script_migrates_database_before_starting_uvicorn() -> None:
    script = (Path(__file__).resolve().parents[2] / "scripts" / "run-api-local.ps1").read_text(encoding="utf-8")

    assert "py -3 -m alembic upgrade head" in script
    assert script.index("py -3 -m alembic upgrade head") < script.index("py -3 -m uvicorn")


def test_console_startup_preserves_operator_auto_execute_setting_and_rotates_logs() -> None:
    root = Path(__file__).resolve().parents[2]
    console_script = (root / "scripts" / "start_paper_console.ps1").read_text(encoding="utf-8")
    api_script = (root / "scripts" / "run-api-local.ps1").read_text(encoding="utf-8")

    assert '$env:BINANCE_AUTO_EXECUTE = "false"' not in console_script.splitlines()
    assert "Rotate-RuntimeLog" in api_script
    assert '$env:LOG_LEVEL = "INFO"' in api_script
    assert "create_relational_schema" not in console_script
    assert "adopt_complete_legacy_sqlite_schema" in api_script
    assert "--no-access-log" in api_script
