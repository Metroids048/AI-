from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cloud_launcher_reuses_the_existing_api_and_v2_scheduler_entries() -> None:
    source = (ROOT / "deploy" / "cloud" / "start_cloud_runtime.sh").read_text(encoding="utf-8")

    assert "-m apps.api.local_server" in source
    assert "--local-console" in source
    assert "scripts/run-local-paper-scheduler.py" in source
    assert '--engine "v2_active"' in source
    assert "PAPER_CONSOLE_API_ONLY=true" in source
    assert "SCHEDULER_PID_PATH" in source


def test_cloud_launcher_does_not_introduce_an_alternate_scheduler_or_celery_writer() -> None:
    source = (ROOT / "deploy" / "cloud" / "start_cloud_runtime.sh").read_text(encoding="utf-8")

    assert "celery" not in source.lower()
    assert "RuntimeScheduler" not in source
    assert "apps.api.main" not in source
