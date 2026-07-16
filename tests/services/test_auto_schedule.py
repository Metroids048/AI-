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
            execution_profile={"strategy_lane": "directional", "auto_schedule_enabled": True},
        )
    )
    scheduled_run_ids: list[str] = []

    def record_cycle(paper_run_id: str, request_payload: dict) -> dict:
        scheduled_run_ids.append(paper_run_id)
        return {"paper_run_id": paper_run_id}

    monkeypatch.setattr(tasks.run_paper_runtime_cycle, "run", record_cycle)

    result = tasks.run_all_paper_runtime_cycles.run({"timeframe": "1m"})

    assert result["paper_runs"] == 1
    assert scheduled_run_ids == [automatic_run.paper_run_id]
    assert manual_run.paper_run_id not in scheduled_run_ids
