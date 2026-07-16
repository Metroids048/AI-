from __future__ import annotations

from pathlib import Path

from services.data.service import DEFAULT_BINANCE_TOP20
from services.execution.bootstrap import (
    AUTO_PAPER_RUNTIME_KEY,
    AUTO_PAPER_TECHNICAL_KEY,
    LINK_VERIFICATION_RUNTIME_KEY,
    LINK_VERIFICATION_STRATEGY_KEY,
    OPERATOR_EXPERIENCE_STRATEGY_KEY,
    bootstrap_auto_trading_paper_run,
    bootstrap_auto_trading_technical_paper_run,
    bootstrap_link_verification_strategy,
    bootstrap_local_paper_runtime,
    bootstrap_operator_experience_strategy,
    bootstrap_paper_testnet_mirror,
    bootstrap_pause_legacy_paper_runs,
    default_mirror_to_gateway,
)
from services.execution.paper import PaperOrchestrationService
from shared.config import settings
from shared.models import PaperRun, RunStatus


def test_default_mirror_to_gateway_stays_disabled_until_cost_gate_is_explicitly_armed(monkeypatch) -> None:
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
    assert default_mirror_to_gateway() is False

    monkeypatch.setattr(settings, "live_trading_enabled", True)
    assert default_mirror_to_gateway() is False


def test_prepare_run_keeps_mirror_disabled_when_credentials_present(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    prepared = PaperOrchestrationService().prepare_run(
        PaperRun(strategy_id="s1", symbol_scope=["BTC/USDT"], execution_profile={})
    )
    assert prepared.execution_profile.get("mirror_to_gateway") is False


def test_prepare_run_defaults_to_full_fixed_operator_universe() -> None:
    prepared = PaperOrchestrationService().prepare_run(PaperRun(strategy_id="strategy-top20"))

    assert prepared.symbol_scope == list(DEFAULT_BINANCE_TOP20)
    assert prepared.selection_basis == "fixed_operator_top20"
    assert prepared.symbol_scope[:2] == ["BTC/USDT", "ETH/USDT"]


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
    assert carry_run.execution_profile.get("execution_mode") == "paper_only"
    assert technical_run.execution_profile.get("execution_mode") == "paper_only"
    assert carry_run.execution_profile.get("mirror_to_gateway") is False
    assert technical_run.execution_profile.get("mirror_to_gateway") is False
    assert carry_run.execution_profile.get("cost_gate_verified") is False
    assert carry_run.selection_basis == "fixed_operator_top20"
    assert carry_run.candidate_symbols == list(DEFAULT_BINANCE_TOP20)

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


def test_bootstrap_preserves_armed_testnet_cost_gate(db_session, monkeypatch) -> None:
    from services.strategy_library import PaperRunRepository

    monkeypatch.setattr(settings, "binance_api_key", "key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret")
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)

    run_id = bootstrap_auto_trading_technical_paper_run()
    assert run_id is not None
    repo = PaperRunRepository(db_session)
    run = repo.get_paper_run(run_id)
    assert run is not None
    repo.update_paper_run(
        run_id,
        execution_profile={
            **run.execution_profile,
            "execution_mode": "binance_simulation_first",
            "mirror_to_gateway": True,
            "cost_gate_verified": True,
            "testnet_acceptance_verified_at": "2026-07-12T00:00:00+00:00",
        },
    )

    again = bootstrap_auto_trading_technical_paper_run()
    assert again == run_id
    refreshed = repo.get_paper_run(run_id)
    assert refreshed is not None
    assert refreshed.execution_profile.get("cost_gate_verified") is True
    assert refreshed.execution_profile.get("mirror_to_gateway") is True
    assert refreshed.execution_profile.get("execution_mode") == "binance_simulation_first"
    assert refreshed.execution_profile.get("testnet_acceptance_verified_at") == "2026-07-12T00:00:00+00:00"
    assert refreshed.execution_profile.get("max_symbols") == 20


def test_has_verified_testnet_acceptance_ignores_recent_task_window(db_session) -> None:
    from datetime import UTC, datetime, timedelta

    from services.strategy_library import AgentTaskRepository, models
    from shared.models import AgentTask

    repo = AgentTaskRepository(db_session)
    acceptance = repo.create_task(
        AgentTask(
            agent_type="execution",
            task_type="testnet_acceptance",
            task_status="completed",
            input_ref="acceptance-proof",
            output_payload={
                "run_status": "completed",
                "completed_symbols": [f"S{i}" for i in range(20)],
                "filled_order_count": 40,
                "final_open_position_count": 0,
                "final_open_order_count": 0,
            },
        )
    )
    # Force acceptance outside the generic recent-task window.
    row = db_session.get(models.AgentTask, acceptance.agent_task_id)
    assert row is not None
    row.created_at = datetime.now(UTC) - timedelta(days=2)
    db_session.commit()

    for index in range(60):
        repo.create_task(
            AgentTask(
                agent_type="review",
                task_type="noise",
                task_status="completed",
                input_ref=f"noise-{index}",
                output_payload={"index": index},
            )
        )

    assert repo.has_verified_testnet_acceptance() is True
    recent = repo.list_tasks(limit=50)
    assert all(task.task_type != "testnet_acceptance" for task in recent)


def test_bootstrap_operator_experience_strategy_uses_valid_disabled_research_state(db_session) -> None:
    from services.strategy_library import StrategyRepository

    strategy_id = bootstrap_operator_experience_strategy()

    strategy = StrategyRepository(db_session).get_strategy(strategy_id or "")
    assert strategy is not None
    assert strategy.strategy_key == OPERATOR_EXPERIENCE_STRATEGY_KEY
    assert strategy.paper_status is RunStatus.NOT_STARTED


def test_bootstrap_link_verification_strategy_creates_isolated_paper_run(db_session) -> None:
    from services.strategy_library import PaperRunRepository, StrategyRepository

    paper_run_id = bootstrap_link_verification_strategy()

    assert paper_run_id is not None
    paper_run = PaperRunRepository(db_session).get_paper_run(paper_run_id)
    assert paper_run is not None
    assert paper_run.execution_profile.get("strategy_lane") == "link_verification"
    assert paper_run.execution_profile.get("auto_paper_runtime_key") == LINK_VERIFICATION_RUNTIME_KEY

    strategy = StrategyRepository(db_session).get_strategy(paper_run.strategy_id or "")
    assert strategy is not None
    assert strategy.strategy_key == LINK_VERIFICATION_STRATEGY_KEY
    assert strategy.rules.entry_rules.get("link_verification_only") is True
    assert strategy.rules.entry_rules.get("default_enabled_for_auto_trading") is False


def test_bootstrap_link_verification_strategy_is_idempotent(db_session) -> None:
    from services.strategy_library import PaperRunRepository

    first_id = bootstrap_link_verification_strategy()
    second_id = bootstrap_link_verification_strategy()

    assert first_id == second_id
    repo = PaperRunRepository(db_session)
    matching = [
        run
        for run in repo.list_paper_runs()
        if run.execution_profile.get("auto_paper_runtime_key") == LINK_VERIFICATION_RUNTIME_KEY
    ]
    assert len(matching) == 1


def test_bootstrap_local_paper_runtime_does_not_auto_create_link_verification_run(db_session) -> None:
    from services.strategy_library import PaperRunRepository

    bootstrap_local_paper_runtime(seed_ohlcv=False)

    repo = PaperRunRepository(db_session)
    matching = [
        run
        for run in repo.list_paper_runs()
        if run.execution_profile.get("auto_paper_runtime_key") == LINK_VERIFICATION_RUNTIME_KEY
    ]
    assert matching == []


def test_console_launcher_migrates_database_without_relaying_api_streams() -> None:
    script = (Path(__file__).resolve().parents[2] / "scripts" / "launch-paper-console.ps1").read_text(encoding="utf-8")

    assert "scripts/prepare_database.py --database-url $SqliteUrl" in script
    assert script.index("scripts/prepare_database.py --database-url $SqliteUrl") < script.index(
        'Start-Process -FilePath $env:AGENT_PYTHON'
    )
    assert 'Start-Process -FilePath $env:AGENT_PYTHON' in script
    assert '"--log-level", "warning"' in script
    assert '"--log-level", "warning", "--local-console")' in script
    assert "run-api-local.ps1" not in script
    # API is launched directly without a PowerShell stream relay.  The separate
    # scheduler deliberately owns stdout/stderr files for health diagnostics.
    api_start = script.index('"-m", "apps.api.local_server"')
    api_block = script[api_start : script.index("Set-Content -LiteralPath $ApiPidFile", api_start)]
    assert "-RedirectStandardOutput" not in api_block
    assert "-RedirectStandardError" not in api_block
    assert "-RedirectStandardOutput $SchedulerLog" in script
    assert "-RedirectStandardError $SchedulerErrorLog" in script
    assert 'return $commandLine -match "--local-console"' in script
    assert "py -3" not in script


def test_local_api_runner_starts_uvicorn_without_powershell_stream_relay() -> None:
    script = (Path(__file__).resolve().parents[2] / "scripts" / "run-api-local.ps1").read_text(encoding="utf-8")

    assert "Start-Process -FilePath $env:AGENT_PYTHON" in script
    assert "Wait-Process -Id $apiProcess.Id" not in script
    assert "& $env:AGENT_PYTHON -m apps.api.local_server" not in script
    assert "-RedirectStandardOutput" not in script
    assert "-RedirectStandardError" not in script
    assert '$env:PAPER_CONSOLE_DISABLE_LIVE_WS = "true"' in script
    assert '$env:PAPER_CONSOLE_SKIP_BACKGROUND_BOOTSTRAP = "true"' in script


def test_console_uses_a_separate_local_scheduler_process() -> None:
    root = Path(__file__).resolve().parents[2]
    launcher = (root / "scripts" / "launch-paper-console.ps1").read_text(encoding="utf-8")
    scheduler = (root / "scripts" / "run-local-paper-scheduler.py").read_text(encoding="utf-8")

    assert '$env:PAPER_CONSOLE_API_ONLY = "true"' in launcher
    assert "run-local-paper-scheduler.py" in launcher
    assert "bootstrap_local_paper_runtime(seed_ohlcv=False)" in scheduler
    assert "scheduler.start()" in scheduler


def test_console_defaults_to_a_nonblocked_api_port_and_forwards_it_to_vite() -> None:
    launcher_path = Path(__file__).resolve().parents[2] / "scripts" / "launch-paper-console.ps1"
    launcher = launcher_path.read_text(encoding="utf-8")

    assert "[int]$ApiPort = 8016" in launcher
    assert '$env:VITE_API_BASE_URL = "http://127.0.0.1:$ApiPort"' in launcher


def test_console_startup_preserves_operator_auto_execute_setting_and_rotates_logs() -> None:
    root = Path(__file__).resolve().parents[2]
    console_script = (root / "scripts" / "start_paper_console.ps1").read_text(encoding="utf-8")
    api_script = (root / "scripts" / "run-api-local.ps1").read_text(encoding="utf-8")
    launcher_script = (root / "scripts" / "launch-paper-console.ps1").read_text(encoding="utf-8")

    assert '$env:BINANCE_AUTO_EXECUTE = "false"' not in console_script.splitlines()
    assert "Reset-LogFile $ApiLog" in launcher_script
    assert '$env:LOG_LEVEL = "INFO"' in api_script
    assert "create_relational_schema" not in console_script
    assert "scripts/prepare_database.py" in launcher_script
    assert 'Start-Process -FilePath $env:AGENT_PYTHON' in launcher_script
    assert "apps.api.local_server" in api_script
    assert '"--log-level", "warning"' in api_script
    assert '$env:BINANCE_HTTPS_PROXY = $env:HTTPS_PROXY' in launcher_script
    assert "py -3" not in console_script
