from __future__ import annotations

import pytest


def test_natural_mode_requires_testnet_and_non_live_environment(monkeypatch) -> None:
    from services.execution.natural_testnet_mode import natural_testnet_mode_requested

    monkeypatch.setenv("V2_NATURAL_E2E_ENABLED", "true")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    assert natural_testnet_mode_requested() is True

    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    assert natural_testnet_mode_requested() is False


def test_natural_mode_is_off_without_process_flag(monkeypatch) -> None:
    from services.execution.natural_testnet_mode import natural_testnet_mode_requested

    monkeypatch.delenv("V2_NATURAL_E2E_ENABLED", raising=False)
    monkeypatch.setenv("BINANCE_USE_TESTNET", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    assert natural_testnet_mode_requested() is False


def test_normal_scheduler_entry_uses_natural_process_authority_only(monkeypatch) -> None:
    from services.execution import v2_scheduler_entry

    captured: dict[str, object] = {}

    def capture(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured.update(kwargs)
        return {"status": "captured"}

    monkeypatch.setattr(v2_scheduler_entry, "_execute_v2_automated_trading_cycles", capture)
    monkeypatch.setenv("V2_NATURAL_E2E_ENABLED", "true")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

    result = v2_scheduler_entry.execute_v2_automated_trading_cycles({"enable_natural_testnet": True})

    assert result == {"status": "captured"}
    assert captured["canary_acceptance"] is True
    assert captured["cycle_source"] == "natural_testnet_sampling"


def test_request_payload_cannot_enable_natural_authority(monkeypatch) -> None:
    from services.execution import v2_scheduler_entry

    captured: dict[str, object] = {}

    def capture(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured.update(kwargs)
        return {"status": "captured"}

    monkeypatch.setattr(v2_scheduler_entry, "_execute_v2_automated_trading_cycles", capture)
    monkeypatch.delenv("V2_NATURAL_E2E_ENABLED", raising=False)
    monkeypatch.setenv("BINANCE_USE_TESTNET", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

    v2_scheduler_entry.execute_v2_automated_trading_cycles({"enable_natural_testnet": True})

    assert captured["canary_acceptance"] is False
    assert captured["cycle_source"] == "automated_trading_v2"


@pytest.mark.asyncio
async def test_runtime_scheduler_publishes_natural_canary_authority(monkeypatch) -> None:
    from services.automated_trading.domain.enums import V2ExecutionMode
    from services.automated_trading.infrastructure import runtime_lock
    from services.automated_trading.infrastructure.runtime_lock import EngineActivation, EngineActivationConfig
    from services.execution import scheduler as scheduler_module
    from services.execution.scheduler import RuntimeScheduler

    config = EngineActivationConfig(
        v2_activation=EngineActivation.ACTIVE,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        allow_legacy_writer=False,
        warnings=[],
    )
    monkeypatch.setenv("V2_NATURAL_E2E_ENABLED", "true")
    monkeypatch.setenv("BINANCE_USE_TESTNET", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(runtime_lock, "resolve_engine_activation", lambda _settings: config)
    monkeypatch.setattr(
        scheduler_module,
        "_resolve_runtime_scheduler_jobs",
        lambda _config: frozenset({"automated_trading_v2_cycle"}),
    )
    monkeypatch.setattr(scheduler_module, "_active_entry_authorization", lambda: (True, True, ()))
    monkeypatch.setattr(
        scheduler_module,
        "_active_execution_strategy_identity",
        lambda: ("production_strategy_pending", False),
    )
    monkeypatch.setattr(scheduler_module, "_external_baseline_capture", lambda: (True, {}, "test"))

    scheduler = RuntimeScheduler(coordinator=object(), paper_cycle_seconds=3600)
    scheduler.start()
    try:
        assert scheduler.status.natural_testnet_enabled is True
        assert scheduler.status.entry_authority == "TESTNET_CANARY"
        assert scheduler.status.entry_authorized is True
        assert scheduler.status.active_entry_strategy == "testnet_sampling_v2"
        assert scheduler.status.production_authorization_state == "PENDING"
        assert scheduler.status.promotion_eligible is False
        assert scheduler.status.trading_state == "TRADING"
    finally:
        await scheduler.stop()


def test_launcher_checks_scheduler_natural_mode_truth() -> None:
    from pathlib import Path

    source = Path("scripts/launch-paper-console.ps1").read_text(encoding="utf-8")
    assert "natural_testnet_enabled" in source
    assert "targetNaturalTestnet" in source
    assert "natural_testnet_enabled" in source[source.index("function Test-SchedulerHealthy") :]


def test_launcher_safety_stop_recovery_is_not_natural_only() -> None:
    from pathlib import Path

    source = Path("scripts/launch-paper-console.ps1").read_text(encoding="utf-8")
    restore = source[source.index("function Restore-EntryAfterStartupSafetyStop") :]
    restore = restore[: restore.index("function Stop-ProjectApiProcesses")]

    assert "-not $PreserveExternalTestnetBaseline" in restore
    assert "$reason = if ($EnableNaturalTestnet)" in restore
    assert "-not $EnableNaturalTestnet -or" not in restore
    assert "Get-PersistedEntryControl" in source
    assert "STARTUP_SAFETY_STOP:*" in restore
    assert "controlSafetyStop" in restore
