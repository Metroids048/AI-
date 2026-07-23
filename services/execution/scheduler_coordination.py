"""Database-backed scheduler leadership and per-slot cycle claims."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from services.strategy_library import models
from shared.config import settings


def _db_time(value: datetime) -> datetime:
    value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return value.replace(tzinfo=None)


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
            takeover = lease.owner_id != self.instance_id or lease.expires_at <= observed_at
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
        with self.session_factory() as session:
            row = models.SchedulerCycle(
                job_name=job_name,
                scheduled_for=_db_time(scheduled_for),
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
                return CycleClaim(claimed=False)
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
