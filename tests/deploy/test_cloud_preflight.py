from __future__ import annotations

from pathlib import Path

import pytest


def _module():
    from deploy.cloud import cloud_preflight

    return cloud_preflight


def _env(tmp_path: Path) -> dict[str, str]:
    db_path = tmp_path / "runtime" / "ai-quant.db"
    state_path = tmp_path / "runtime" / "scheduler-state.json"
    baseline_path = tmp_path / "runtime" / "testnet-external-baseline.json"
    baseline_path.write_text('{"execution_mode":"BINANCE_TESTNET","positions":{}}', encoding="utf-8")
    return {
        "POSTGRES_URL": f"sqlite:///{db_path.as_posix()}",
        "LOCAL_SCHEDULER_STATE_PATH": str(state_path),
        "AUTOMATED_TRADING_ENGINE": "v2_active",
        "BINANCE_USE_TESTNET": "true",
        "LIVE_TRADING_ENABLED": "false",
        "BINANCE_AUTO_EXECUTE": "true",
        "BINANCE_API_KEY": "test-key",
        "BINANCE_API_SECRET": "test-secret",
        "V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS": "true",
        "V2_EXTERNAL_BASELINE_JSON": "{}",
        "V2_EXTERNAL_BASELINE_PATH": str(baseline_path),
        "V2_EXTERNAL_BASELINE_SOURCE": f"persistent_file:{baseline_path}",
    }


def test_cloud_preflight_accepts_explicit_active_testnet_configuration(tmp_path: Path) -> None:
    module = _module()
    (tmp_path / "runtime").mkdir()
    result = module.validate_environment(_env(tmp_path))

    assert result.engine == "v2_active"
    assert result.execution_mode == "BINANCE_TESTNET"
    assert result.database_path == tmp_path / "runtime" / "ai-quant.db"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("AUTOMATED_TRADING_ENGINE", "v2_shadow", "AUTOMATED_TRADING_ENGINE must be v2_active"),
        ("BINANCE_USE_TESTNET", "false", "BINANCE_USE_TESTNET must be true"),
        ("LIVE_TRADING_ENABLED", "true", "LIVE_TRADING_ENABLED must be false"),
        ("BINANCE_AUTO_EXECUTE", "false", "BINANCE_AUTO_EXECUTE must be true"),
        ("BINANCE_API_KEY", "", "BINANCE_API_KEY is required"),
        ("BINANCE_API_SECRET", "", "BINANCE_API_SECRET is required"),
    ],
)
def test_cloud_preflight_fails_closed_for_unsafe_or_incomplete_environment(
    tmp_path: Path, key: str, value: str, message: str
) -> None:
    module = _module()
    (tmp_path / "runtime").mkdir()
    env = _env(tmp_path)
    env[key] = value

    with pytest.raises(module.PreflightError, match=message):
        module.validate_environment(env)


def test_cloud_preflight_rejects_live_scheduler_pid(tmp_path: Path) -> None:
    module = _module()
    (tmp_path / "runtime").mkdir()
    env = _env(tmp_path)
    pid_file = tmp_path / "scheduler.pid"
    pid_file.write_text("1234", encoding="ascii")

    with pytest.raises(module.PreflightError, match="scheduler PID already exists"):
        module.validate_environment(env, scheduler_pid_path=pid_file, pid_is_alive=lambda pid: pid == 1234)


def test_cloud_preflight_requires_the_persisted_external_baseline(tmp_path: Path) -> None:
    module = _module()
    (tmp_path / "runtime").mkdir()
    env = _env(tmp_path)
    env["V2_EXTERNAL_BASELINE_PATH"] = str(tmp_path / "runtime" / "missing.json")

    with pytest.raises(module.PreflightError, match="V2 external baseline file is missing"):
        module.validate_environment(env)


def test_cloud_preflight_reports_memory_without_claiming_a_local_cloud_pass(tmp_path: Path) -> None:
    module = _module()
    (tmp_path / "runtime").mkdir()
    env = _env(tmp_path)
    result = module.validate_environment(env, memory_snapshot=lambda: {"available_mb": 512, "total_mb": 1024})

    assert result.memory["available_mb"] == 512
    assert result.resource_gate == "UNKNOWN_EXTERNAL_VM"
