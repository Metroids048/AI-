from __future__ import annotations

from services.execution import tasks
from services.strategy_library import PaperRunRepository
from shared.models import PaperRun


def test_runtime_cycle_ignores_running_manual_paper_runs_without_auto_schedule_permission(
    db_session, monkeypatch
) -> None:
    repo = PaperRunRepository(db_session)
    manual_run = repo.create_paper_run(
        PaperRun(
            strategy_id="manual-paper",
            paper_status="running",
            execution_profile={"strategy_lane": "manual"},
        )
    )
    automatic_run = repo.create_paper_run(
        PaperRun(
            strategy_id="automatic-paper",
            paper_status="running",
            execution_profile={
                "strategy_lane": "directional",
                "auto_schedule_enabled": True,
                "execution_mode": "binance_testnet",
                "mirror_to_gateway": True,
            },
        )
    )
    scheduled_run_ids: list[str] = []

    def record_cycle(paper_run_id: str, request_payload: dict) -> dict:
        scheduled_run_ids.append(paper_run_id)
        return {"paper_run_id": paper_run_id}

    monkeypatch.setattr(tasks.run_paper_runtime_cycle, "run", record_cycle)

    result = tasks.run_all_paper_runtime_cycles.run({"timeframe": "1m"})

    assert result["paper_runs"] == 1
    assert result["slot"] == "tight_entry"
    assert scheduled_run_ids == [automatic_run.paper_run_id]
    assert manual_run.paper_run_id not in scheduled_run_ids


def test_tight_entry_slot_excludes_local_observation_runs(db_session, monkeypatch) -> None:
    repo = PaperRunRepository(db_session)
    observation = repo.create_paper_run(
        PaperRun(
            strategy_id="observation-paper",
            paper_status="running",
            execution_profile={
                "strategy_lane": "signal_observation",
                "auto_schedule_enabled": True,
                "execution_mode": "local_paper",
                "mirror_to_gateway": False,
            },
        )
    )
    testnet = repo.create_paper_run(
        PaperRun(
            strategy_id="testnet-paper",
            paper_status="running",
            execution_profile={
                "strategy_lane": "directional",
                "auto_schedule_enabled": True,
                "execution_mode": "binance_testnet",
                "mirror_to_gateway": True,
            },
        )
    )
    scheduled_run_ids: list[str] = []

    def record_cycle(paper_run_id: str, request_payload: dict) -> dict:
        scheduled_run_ids.append(paper_run_id)
        return {"paper_run_id": paper_run_id}

    monkeypatch.setattr(tasks.run_paper_runtime_cycle, "run", record_cycle)

    tight = tasks.run_all_paper_runtime_cycles.run({"timeframe": "15m"})
    assert tight["paper_runs"] == 1
    assert tight["slot"] == "tight_entry"
    assert scheduled_run_ids == [testnet.paper_run_id]

    scheduled_run_ids.clear()
    observation_result = tasks.run_observation_paper_runtime_cycles.run({"timeframe": "15m"})
    assert observation_result["paper_runs"] == 1
    assert observation_result["slot"] == "observation"
    assert scheduled_run_ids == [observation.paper_run_id]
