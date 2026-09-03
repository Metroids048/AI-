from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.execution.scheduler_coordination import SchedulerCoordinator, supervisor_lease_name, validate_fence
from services.strategy_library.models import Base


def _coordinator(tmp_path, instance_id: str, *, process_id: int | None = None) -> SchedulerCoordinator:
    db_path = tmp_path / "coordination.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    return SchedulerCoordinator(
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        instance_id=instance_id,
        hostname="test-host",
        process_id=process_id if process_id is not None else os.getpid(),
    )


def _paired_coordinators(tmp_path) -> tuple[SchedulerCoordinator, SchedulerCoordinator]:
    """Two instance IDs sharing one DB; distinct fake PIDs keep reclaim logic honest."""
    db_path = tmp_path / "coordination.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    first = SchedulerCoordinator(
        session_factory=factory,
        instance_id="scheduler-a",
        hostname="test-host",
        process_id=10101,
    )
    second = SchedulerCoordinator(
        session_factory=factory,
        instance_id="scheduler-b",
        hostname="test-host",
        process_id=20202,
    )
    return first, second


def test_supervisor_lease_is_exclusive_per_database_and_execution_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "services.execution.scheduler_coordination._local_pid_is_alive",
        lambda pid: True,
    )
    first, second = _paired_coordinators(tmp_path)
    lease_name = supervisor_lease_name("sqlite:///same.db", "v2_active")
    now = datetime(2026, 9, 3, tzinfo=UTC)

    assert lease_name == supervisor_lease_name("sqlite:///same.db", "v2_active")
    assert lease_name != supervisor_lease_name("sqlite:///same.db", "v2_shadow")
    assert lease_name != supervisor_lease_name("sqlite:///other.db", "v2_active")

    assert first.acquire_or_renew_lease(lease_name=lease_name, now=now, ttl_seconds=30)
    first_token = first.fencing_token(lease_name=lease_name)
    assert first_token == 1
    assert not second.acquire_or_renew_lease(lease_name=lease_name, now=now, ttl_seconds=30)

    assert second.acquire_or_renew_lease(lease_name=lease_name, now=now + timedelta(seconds=31), ttl_seconds=30)
    second_token = second.fencing_token(lease_name=lease_name)
    assert second_token == 2
    second.release_lease(lease_name=lease_name, fencing_token=first_token)
    with second.session_factory() as session:
        assert validate_fence(
            session,
            lease_name=lease_name,
            owner_id="scheduler-b",
            fencing_token=second_token,
            now=now + timedelta(seconds=32),
        )


def test_only_one_instance_can_hold_the_paper_scheduler_lease(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "services.execution.scheduler_coordination._local_pid_is_alive",
        lambda pid: True,
    )
    first, second = _paired_coordinators(tmp_path)
    now = datetime(2026, 7, 22, tzinfo=UTC)

    assert first.acquire_or_renew_lease(now=now, ttl_seconds=90) is True
    assert second.acquire_or_renew_lease(now=now, ttl_seconds=90) is False
    assert second.acquire_or_renew_lease(now=now + timedelta(seconds=91), ttl_seconds=90) is True


def test_takeover_increments_fencing_token_and_rejects_stale_owner(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "services.execution.scheduler_coordination._local_pid_is_alive",
        lambda pid: True,
    )
    first, second = _paired_coordinators(tmp_path)
    now = datetime.now(UTC)

    assert first.acquire_or_renew_lease(now=now, ttl_seconds=90)
    first_token = first.fencing_token()
    assert first_token == 1
    assert second.acquire_or_renew_lease(now=now + timedelta(seconds=91), ttl_seconds=90)
    second_token = second.fencing_token()
    assert second_token == 2
    assert (
        first.acquire_or_renew_lease(
            now=now + timedelta(seconds=92),
            ttl_seconds=90,
            fencing_token=first_token,
        )
        is False
    )
    with second.session_factory() as session:
        assert not validate_fence(
            session,
            lease_name="paper_runtime_cycle",
            owner_id="scheduler-a",
            fencing_token=first_token,
            now=now + timedelta(seconds=92),
        )
        assert validate_fence(
            session,
            lease_name="paper_runtime_cycle",
            owner_id="scheduler-b",
            fencing_token=second_token,
            now=now + timedelta(seconds=92),
        )


def test_same_owner_reacquires_expired_lease_with_new_fencing_token(tmp_path) -> None:
    coordinator = _coordinator(tmp_path, "scheduler-a")
    now = datetime.now(UTC)

    assert coordinator.acquire_or_renew_lease(now=now, ttl_seconds=30)
    assert coordinator.fencing_token() == 1
    coordinator.release_lease()
    assert coordinator.acquire_or_renew_lease(now=now + timedelta(seconds=1), ttl_seconds=30)
    assert coordinator.fencing_token() == 2


def test_stale_same_owner_release_cannot_expire_new_fencing_token(tmp_path) -> None:
    coordinator = _coordinator(tmp_path, "scheduler-a")
    now = datetime.now(UTC)

    assert coordinator.acquire_or_renew_lease(now=now, ttl_seconds=30)
    first_token = coordinator.fencing_token()
    assert first_token == 1
    assert coordinator.acquire_or_renew_lease(now=now + timedelta(seconds=31), ttl_seconds=30)
    second_token = coordinator.fencing_token()
    assert second_token == 2

    coordinator.release_lease(fencing_token=first_token)

    with coordinator.session_factory() as session:
        assert validate_fence(
            session,
            lease_name="paper_runtime_cycle",
            owner_id="scheduler-a",
            fencing_token=second_token,
            now=now + timedelta(seconds=32),
        )


def test_two_instances_claim_each_of_96_accelerated_24h_slots_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "services.execution.scheduler_coordination._local_pid_is_alive",
        lambda pid: True,
    )
    first, second = _paired_coordinators(tmp_path)
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


def test_dead_local_owner_lease_can_be_reclaimed_before_ttl(tmp_path, monkeypatch) -> None:
    first = _coordinator(tmp_path, "scheduler-a", process_id=424242)
    second = SchedulerCoordinator(
        session_factory=first.session_factory,
        instance_id="scheduler-b",
        hostname="test-host",
        process_id=os.getpid(),
    )
    now = datetime(2026, 7, 28, 4, 10, tzinfo=UTC)
    assert first.acquire_or_renew_lease(now=now, ttl_seconds=900) is True
    monkeypatch.setattr(
        "services.execution.scheduler_coordination._local_pid_is_alive",
        lambda pid: False,
    )
    assert second.acquire_or_renew_lease(now=now + timedelta(seconds=5), ttl_seconds=90) is True
    assert second.fencing_token() == 2


def test_dead_local_claimed_slot_can_be_reclaimed(tmp_path, monkeypatch) -> None:
    first = _coordinator(tmp_path, "scheduler-a", process_id=424242)
    second = SchedulerCoordinator(
        session_factory=first.session_factory,
        instance_id="scheduler-b",
        hostname="test-host",
        process_id=os.getpid(),
    )
    scheduled_for = datetime(2026, 7, 28, 4, 20, tzinfo=UTC)
    claim = first.claim_cycle(job_name="paper_runtime_cycle", scheduled_for=scheduled_for)
    assert claim.claimed is True
    monkeypatch.setattr(
        "services.execution.scheduler_coordination._local_pid_is_alive",
        lambda pid: False,
    )
    reclaimed = second.claim_cycle(job_name="paper_runtime_cycle", scheduled_for=scheduled_for)
    assert reclaimed.claimed is True
    assert reclaimed.scheduler_cycle_id == claim.scheduler_cycle_id
    cycles = second.list_cycles(job_name="paper_runtime_cycle")
    assert len(cycles) == 1
    assert cycles[0].scheduler_instance_id == "scheduler-b"
    assert cycles[0].failure_reason == "reclaimed_from_dead_local_owner"
