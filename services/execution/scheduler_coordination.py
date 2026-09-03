"""Database-backed scheduler leadership and per-slot cycle claims."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from services.strategy_library import models
from shared.config import settings


def _db_time(value: datetime) -> datetime:
    value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return value.replace(tzinfo=None)


def supervisor_lease_name(database_url: str, execution_mode: str) -> str:
    """Return a stable, non-sensitive lease key for one runtime boundary."""
    identity = f"{database_url.strip()}|{execution_mode.strip()}"
    return f"automated_trading_supervisor:{sha256(identity.encode('utf-8')).hexdigest()}"


def _local_pid_is_alive(pid: int | None) -> bool:
    """Best-effort liveness probe for same-host lease reclaim after hard kills."""
    if pid is None or int(pid) <= 0:
        return False
    pid_i = int(pid)
    if os.name == "nt":
        import ctypes

        # OpenProcess is more reliable than os.kill(pid, 0) on Windows for
        # distinguishing live vs gone PIDs across privilege boundaries.
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid_i)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid_i, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class CycleClaim:
    claimed: bool
    scheduler_cycle_id: str | None = None


class SchedulerCoordinator:
    """Coordinate schedulers across processes using the relational database."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        instance_id: str,
        hostname: str | None = None,
        process_id: int | None = None,
        deployment_sha: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.instance_id = instance_id
        self.hostname = hostname or socket.gethostname()
        self.process_id = process_id or os.getpid()
        self.deployment_sha = deployment_sha or settings.app_build_id
        self._fencing_tokens: dict[str, int] = {}

    def acquire_or_renew_lease(
        self,
        *,
        lease_name: str = "paper_runtime_cycle",
        now: datetime | None = None,
        ttl_seconds: float = 90,
        fencing_token: int | None = None,
    ) -> bool:
        observed_at = _db_time(now or datetime.now(UTC))
        with self.session_factory() as session:
            lease = session.get(models.SchedulerLease, lease_name)
            if lease is None:
                session.add(
                    models.SchedulerLease(
                        lease_name=lease_name,
                        owner_id=self.instance_id,
                        hostname=self.hostname,
                        process_id=self.process_id,
                        acquired_at=observed_at,
                        heartbeat_at=observed_at,
                        expires_at=observed_at + timedelta(seconds=ttl_seconds),
                        fencing_token=1,
                    )
                )
                try:
                    session.commit()
                    self._fencing_tokens[lease_name] = 1
                    return True
                except IntegrityError:
                    session.rollback()
                    return False
            if (
                lease.owner_id == self.instance_id
                and fencing_token is not None
                and lease.fencing_token != fencing_token
            ):
                return False
            # Hard-killed local schedulers leave a long TTL lease; reclaim when the
            # recorded PID is gone on the same host so restarts are not stuck in
            # standby_not_leader until expires_at. Same-PID multi-instance (tests /
            # rare in-process re-entry) must not treat the live owner as dead.
            dead_local_owner = (
                lease.owner_id != self.instance_id
                and lease.hostname == self.hostname
                and int(lease.process_id or 0) != int(self.process_id)
                and not _local_pid_is_alive(lease.process_id)
            )
            if dead_local_owner:
                session.query(models.SchedulerLease).filter(
                    models.SchedulerLease.lease_name == lease_name,
                    models.SchedulerLease.owner_id == lease.owner_id,
                    models.SchedulerLease.process_id == lease.process_id,
                    models.SchedulerLease.hostname == self.hostname,
                ).update(
                    {"expires_at": observed_at},
                    synchronize_session=False,
                )
                session.flush()
            takeover = lease.owner_id != self.instance_id or lease.expires_at <= observed_at or dead_local_owner
            next_token = int(lease.fencing_token or 1) + (1 if takeover else 0)
            updated = (
                session.query(models.SchedulerLease)
                .filter(
                    models.SchedulerLease.lease_name == lease_name,
                    or_(
                        models.SchedulerLease.owner_id == self.instance_id,
                        models.SchedulerLease.expires_at <= observed_at,
                    ),
                )
                .update(
                    {
                        "owner_id": self.instance_id,
                        "hostname": self.hostname,
                        "process_id": self.process_id,
                        "acquired_at": observed_at,
                        "heartbeat_at": observed_at,
                        "expires_at": observed_at + timedelta(seconds=ttl_seconds),
                        "fencing_token": next_token,
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            if updated == 1:
                self._fencing_tokens[lease_name] = next_token
            return updated == 1

    def fencing_token(self, *, lease_name: str = "paper_runtime_cycle") -> int | None:
        with self.session_factory() as session:
            lease = session.get(models.SchedulerLease, lease_name)
            token = int(lease.fencing_token) if lease is not None else None
            if token is not None:
                self._fencing_tokens[lease_name] = token
            return token

    def release_lease(
        self,
        *,
        lease_name: str = "paper_runtime_cycle",
        fencing_token: int | None = None,
    ) -> None:
        with self.session_factory() as session:
            lease = session.get(models.SchedulerLease, lease_name)
            expected_token = fencing_token or self._fencing_tokens.get(lease_name)
            if (
                lease is not None
                and lease.owner_id == self.instance_id
                and expected_token is not None
                and int(lease.fencing_token or 0) == expected_token
            ):
                lease.expires_at = _db_time(datetime.now(UTC))
                session.commit()

    def claim_cycle(
        self,
        *,
        job_name: str,
        scheduled_for: datetime,
        cycle_source: str = "runtime_scheduler",
        run_mode: str = "paper",
        fencing_token: int | None = None,
    ) -> CycleClaim:
        scheduled_for_db = _db_time(scheduled_for)
        with self.session_factory() as session:
            row = models.SchedulerCycle(
                job_name=job_name,
                scheduled_for=scheduled_for_db,
                scheduler_instance_id=self.instance_id,
                cycle_source=cycle_source,
                run_mode=run_mode,
                deployment_sha=self.deployment_sha,
                hostname=self.hostname,
                process_id=self.process_id,
                status="claimed",
                started_at=_db_time(datetime.now(UTC)),
                fencing_token=(fencing_token or self._fencing_tokens.get("paper_runtime_cycle")),
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = (
                    session.query(models.SchedulerCycle)
                    .filter(
                        models.SchedulerCycle.job_name == job_name,
                        models.SchedulerCycle.scheduled_for == scheduled_for_db,
                    )
                    .one_or_none()
                )
                if existing is None:
                    return CycleClaim(claimed=False)
                dead_local_claim = (
                    existing.status == "claimed"
                    and existing.hostname == self.hostname
                    and existing.scheduler_instance_id != self.instance_id
                    and int(existing.process_id or 0) != int(self.process_id)
                    and not _local_pid_is_alive(existing.process_id)
                )
                if not dead_local_claim:
                    return CycleClaim(claimed=False)
                existing.scheduler_instance_id = self.instance_id
                existing.cycle_source = cycle_source
                existing.run_mode = run_mode
                existing.deployment_sha = self.deployment_sha
                existing.hostname = self.hostname
                existing.process_id = self.process_id
                existing.status = "claimed"
                existing.started_at = _db_time(datetime.now(UTC))
                existing.completed_at = None
                existing.failure_reason = "reclaimed_from_dead_local_owner"
                existing.fencing_token = fencing_token or self._fencing_tokens.get("paper_runtime_cycle")
                session.commit()
                return CycleClaim(claimed=True, scheduler_cycle_id=existing.scheduler_cycle_id)
            return CycleClaim(claimed=True, scheduler_cycle_id=row.scheduler_cycle_id)

    def finish_cycle(self, cycle_id: str, *, status: str, failure_reason: str | None = None) -> None:
        with self.session_factory() as session:
            row = session.get(models.SchedulerCycle, cycle_id)
            if row is None:
                return
            row.status = status
            row.completed_at = _db_time(datetime.now(UTC))
            row.failure_reason = failure_reason
            session.commit()

    def list_cycles(self, *, job_name: str | None = None) -> list[Any]:
        with self.session_factory() as session:
            query = session.query(models.SchedulerCycle)
            if job_name is not None:
                query = query.filter(models.SchedulerCycle.job_name == job_name)
            return query.order_by(models.SchedulerCycle.scheduled_for).all()


def validate_fence(
    session: Session,
    *,
    lease_name: str,
    owner_id: str,
    fencing_token: int,
    now: datetime | None = None,
) -> bool:
    observed_at = _db_time(now or datetime.now(UTC))
    lease = session.get(models.SchedulerLease, lease_name)
    return bool(
        lease is not None
        and lease.owner_id == owner_id
        and int(lease.fencing_token or 0) == fencing_token
        and lease.expires_at > observed_at
    )
