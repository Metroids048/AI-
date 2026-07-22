from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.execution.scheduler_coordination import SchedulerCoordinator
from services.strategy_library.models import Base


def _coordinator(tmp_path, instance_id: str) -> SchedulerCoordinator:
    engine = create_engine(f"sqlite:///{(tmp_path / 'coordination.db').as_posix()}")
    Base.metadata.create_all(engine)
    return SchedulerCoordinator(
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        instance_id=instance_id,
        hostname="test-host",
        process_id=123,
    )


def test_only_one_instance_can_hold_the_paper_scheduler_lease(tmp_path) -> None:
    first = _coordinator(tmp_path, "scheduler-a")
    second = _coordinator(tmp_path, "scheduler-b")
    now = datetime(2026, 7, 22, tzinfo=UTC)

    assert first.acquire_or_renew_lease(now=now, ttl_seconds=90) is True
    assert second.acquire_or_renew_lease(now=now, ttl_seconds=90) is False
    assert second.acquire_or_renew_lease(now=now + timedelta(seconds=91), ttl_seconds=90) is True


def test_two_instances_claim_each_of_96_accelerated_24h_slots_once(tmp_path) -> None:
    first = _coordinator(tmp_path, "scheduler-a")
    second = _coordinator(tmp_path, "scheduler-b")
    start = datetime(2026, 7, 22, tzinfo=UTC)
    accepted = 0

    for slot_index in range(96):
        scheduled_for = start + timedelta(minutes=15 * slot_index)
        claims = [
            first.claim_cycle(job_name="paper_runtime_cycle", scheduled_for=scheduled_for),
            second.claim_cycle(job_name="paper_runtime_cycle", scheduled_for=scheduled_for),
        ]
        accepted += sum(claim.claimed for claim in claims)

    assert accepted == 96
    assert len(first.list_cycles(job_name="paper_runtime_cycle")) == 96
