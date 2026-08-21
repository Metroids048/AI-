from __future__ import annotations

import os
import runpy
from pathlib import Path


def test_isolated_scheduler_enforces_v2_shadow_environment(
    monkeypatch,
) -> None:
    for name in (
        "POSTGRES_URL",
        "APP_ENV",
        "AUTOMATED_TRADING_ENGINE",
        "BINANCE_USE_TESTNET",
        "LIVE_TRADING_ENABLED",
        "BINANCE_LIVE_WS_ENABLED",
        "BINANCE_HTTPS_PROXY",
        "BINANCE_HTTP_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    namespace["configure_scheduler_environment"]("sqlite:///C:/runtime.db")

    assert os.environ["POSTGRES_URL"] == "sqlite:///C:/runtime.db"
    assert os.environ["APP_ENV"] == "development"
    assert os.environ["AUTOMATED_TRADING_ENGINE"] == "v2_shadow"
    assert os.environ["BINANCE_USE_TESTNET"] == "true"
    assert os.environ["LIVE_TRADING_ENABLED"] == "false"
    assert os.environ["BINANCE_LIVE_WS_ENABLED"] == "false"


def test_scheduler_supervisor_uses_configured_state_path(monkeypatch, tmp_path) -> None:
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    state_path = tmp_path / "nested" / "scheduler-state.json"
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(state_path))

    namespace["_write_recovery_overlay"](
        state={},
        reason="V2_DECISION_STREAM_STALLED",
        state_name="ENTRY_HOLD",
        attempt=1,
        restart_count=0,
    )

    assert state_path.exists()
    assert namespace["_load_state"]()["entry_authority"] == "NONE"
    assert namespace["_load_state"]()["entry_authority_reason"].startswith("LIVENESS_RECOVERY_HOLD:")


def test_scheduler_supervisor_detects_stalled_critical_task(monkeypatch, tmp_path) -> None:
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))

    class FakeWorker:
        returncode = None

        def poll(self) -> int | None:
            return self.returncode

    class FakeClock:
        @staticmethod
        def monotonic() -> float:
            return 1800.0

    monkeypatch.setitem(namespace, "time", FakeClock)
    monkeypatch.setattr(
        "services.execution.runtime_state.load_external_scheduler_state",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        "services.execution.runtime_state.critical_task_liveness_errors",
        lambda *_args, **_kwargs: ("V2_DECISION_STREAM_STALLED",),
    )

    reason = namespace["_worker_health_reason"](worker=FakeWorker(), started_at=0.0)

    assert reason == "V2_DECISION_STREAM_STALLED"
