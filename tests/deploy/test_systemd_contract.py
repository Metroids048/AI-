from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"


def _read(name: str) -> str:
    return (SYSTEMD / name).read_text(encoding="utf-8")


def test_api_service_is_restartable_and_uses_cloud_launcher() -> None:
    source = _read("ai-quant-api.service")

    assert "ExecStart=" in source
    assert "start_cloud_runtime.sh api" in source
    assert "EnvironmentFile=-/etc/ai-quant/cloud.env" in source
    assert "Restart=always" in source
    assert "RestartSec=" in source
    assert "WantedBy=multi-user.target" in source
    assert "start_cloud_runtime.sh" in source


def test_scheduler_service_is_the_only_runtime_scheduler() -> None:
    source = _read("ai-quant-scheduler.service")

    assert "ExecStart=" in source
    assert "start_cloud_runtime.sh scheduler" in source
    assert "Environment=SCHEDULER_PID_PATH=/var/lib/ai-quant/runtime/scheduler.pid" in source
    assert "run-local-paper-scheduler.py" not in source
    assert "celery" not in source.lower()
    assert "EnvironmentFile=-/etc/ai-quant/cloud.env" in source
    assert "Restart=always" in source
    assert "KillMode=control-group" in source
    assert "WantedBy=multi-user.target" in source


def test_cloud_units_share_a_fixed_working_directory_and_private_environment() -> None:
    for name in ("ai-quant-api.service", "ai-quant-scheduler.service"):
        source = _read(name)
        assert "WorkingDirectory=/opt/ai-quant" in source
        assert "EnvironmentFile=-/etc/ai-quant/cloud.env" in source
