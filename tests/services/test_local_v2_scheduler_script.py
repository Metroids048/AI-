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


def test_worker_initializes_absent_runtime_control_before_scheduler_start() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py").read_text(
        encoding="utf-8"
    )

    bootstrap_call = source.index("bootstrap_local_paper_runtime(seed_ohlcv=False)")
    control_call = source.index("initialize_standard_runtime_entry_control()")
    scheduler_start = source.index("scheduler.start()")

    assert bootstrap_call < control_call < scheduler_start


def test_scheduler_supervisor_uses_configured_state_path(monkeypatch, tmp_path) -> None:
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    state_path = tmp_path / "nested" / "scheduler-state.json"
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(state_path))

    namespace["_write_recovery_overlay"](
        state={
            "entry_authority": "TESTNET_FORWARD",
            "entry_authority_reason": "TESTNET_FORWARD_AUTHORIZED",
            "active_entry_strategy": "validated-forward-v1",
        },
        reason="V2_DECISION_STREAM_STALLED",
        state_name="ENTRY_HOLD",
        attempt=1,
        restart_count=0,
    )

    assert state_path.exists()
    state = namespace["_load_state"]()
    assert state["entry_authority"] == "TESTNET_FORWARD"
    assert state["entry_authority_reason"] == "TESTNET_FORWARD_AUTHORIZED"
    assert state["active_entry_strategy"] == "validated-forward-v1"
    assert state["entry_control_reason"].startswith("LIVENESS_RECOVERY_HOLD:")
    assert state["trading_state"] == "MANAGEMENT_ONLY"


def test_recovery_overlay_preserves_manual_control_ownership(monkeypatch, tmp_path) -> None:
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    state_path = tmp_path / "scheduler-state.json"
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(state_path))

    namespace["_write_recovery_overlay"](
        state={
            "entry_authority": "TESTNET_FORWARD",
            "entry_control_reason": "ENTRY_PAUSED:operator",
        },
        reason="WORKER_EXITED:1",
        state_name="RECOVERING",
        attempt=1,
        restart_count=0,
        entry_hold_owned=False,
    )

    state = namespace["_load_state"]()
    assert state["entry_authority"] == "TESTNET_FORWARD"
    assert state["entry_control_reason"] == "ENTRY_PAUSED:operator"
    assert state["recovery"]["entry_hold"] is False


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


def test_supervisor_keeps_running_after_ten_recoverable_external_failures(monkeypatch, tmp_path) -> None:
    """The fast recovery budget must degrade, never terminate the supervisor."""
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    module_globals = namespace["run_supervisor"].__globals__
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))

    class FakeWorker:
        returncode = None

        def poll(self) -> int | None:
            return self.returncode

    spawned: list[FakeWorker] = []
    overlays: list[dict] = []
    sleeps: list[float] = []
    reasons = iter(["BINANCE_SERVER_TIME_UNAVAILABLE"] * 10 + [None])

    def fake_spawn(*_args, **_kwargs):  # noqa: ANN001
        worker = FakeWorker()
        spawned.append(worker)
        return worker

    def fake_health(**_kwargs):  # noqa: ANN003
        reason = next(reasons)
        if reason is None:
            raise KeyboardInterrupt
        return reason

    monkeypatch.setitem(module_globals, "_spawn_worker", fake_spawn)
    monkeypatch.setitem(module_globals, "_worker_health_reason", fake_health)
    monkeypatch.setitem(module_globals, "_load_state", lambda: {})
    monkeypatch.setitem(module_globals, "_terminate_worker", lambda _worker: None)
    monkeypatch.setitem(module_globals, "_reclaim_stale_locks", lambda _database_url: None)
    monkeypatch.setitem(module_globals, "_write_recovery_overlay", lambda **kwargs: overlays.append(kwargs))
    monkeypatch.setattr(module_globals["time"], "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("services.execution.scheduler.persist_liveness_recovery_hold", lambda _reason: True)

    assert namespace["run_supervisor"]("sqlite:///runtime.db", monitor_seconds=0.1) == 0
    assert len(spawned) == 11
    assert len(overlays) == 10
    assert all(item["state_name"] == "DEGRADED_EXTERNAL" for item in overlays)
    assert overlays[2]["reason"].startswith("RECOVERY_BUDGET_EXHAUSTED_HOLD:")
    recovery_sleeps = [seconds for seconds in sleeps if seconds > 0.1]
    assert recovery_sleeps == [5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 300.0, 300.0, 300.0, 300.0]


def test_supervisor_exits_only_for_explicit_fatal_boot_error(monkeypatch, tmp_path) -> None:
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    module_globals = namespace["run_supervisor"].__globals__
    monkeypatch.setenv("LOCAL_SCHEDULER_STATE_PATH", str(tmp_path / "scheduler-state.json"))

    class FakeWorker:
        def poll(self) -> int | None:
            return 1

    overlays: list[dict] = []
    monkeypatch.setitem(module_globals, "_spawn_worker", lambda *_args, **_kwargs: FakeWorker())
    monkeypatch.setitem(
        module_globals,
        "_worker_health_reason",
        lambda **_kwargs: "FATAL_BOOT_ERROR:invalid_runtime_contract",
    )
    monkeypatch.setitem(module_globals, "_load_state", lambda: {})
    monkeypatch.setitem(module_globals, "_terminate_worker", lambda _worker: None)
    monkeypatch.setitem(module_globals, "_write_recovery_overlay", lambda **kwargs: overlays.append(kwargs))
    monkeypatch.setattr(module_globals["time"], "sleep", lambda _seconds: None)

    assert namespace["run_supervisor"]("sqlite:///runtime.db", monitor_seconds=0.1) == 1
    assert overlays == [
        {
            "state": {},
            "reason": "FATAL_BOOT_ERROR:invalid_runtime_contract",
            "state_name": "FATAL_BOOT_ERROR",
            "attempt": 0,
            "restart_count": 0,
        }
    ]
