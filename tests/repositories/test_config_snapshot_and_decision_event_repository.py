from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.strategy_library.repository import (
    ConfigConflictError,
    ConfigSnapshotRepository,
    DecisionEventRepository,
)
from shared.models import BlockCode, ConfigSnapshot, DecisionEvent, DecisionEventType
from tests.repositories.test_decision_snapshot_repository import _create_paper_run


def test_config_snapshot_uses_optimistic_hash_and_next_cycle_activation(db_session) -> None:
    run = _create_paper_run(db_session)
    repo = ConfigSnapshotRepository(db_session)
    initial = repo.create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=run.paper_run_id,
            config={"risk": {"risk_fraction": "0.05"}},
            created_by="bootstrap",
            effective_cycle_id="cycle-1",
        ),
        base_config_hash=None,
    )
    pending = repo.create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=run.paper_run_id,
            config={"risk": {"risk_fraction": "0.05"}, "timeframe": "15m"},
            created_by="operator",
            effective_cycle_id="NEXT_CYCLE",
            previous_snapshot_id=initial.config_snapshot_id,
        ),
        base_config_hash=initial.config_hash,
    )

    assert repo.get_active(run.paper_run_id).config_hash == initial.config_hash
    assert repo.get_pending(run.paper_run_id).config_hash == pending.config_hash
    activated = repo.activate_pending(run.paper_run_id, cycle_id="cycle-2")
    assert activated is not None
    assert repo.get_active(run.paper_run_id).config_hash == pending.config_hash


def test_config_snapshot_rejects_stale_base_hash(db_session) -> None:
    run = _create_paper_run(db_session)
    repo = ConfigSnapshotRepository(db_session)
    repo.create_snapshot(
        ConfigSnapshot.create(
            paper_run_id=run.paper_run_id,
            config={"version": 1},
            created_by="bootstrap",
            effective_cycle_id="cycle-1",
        ),
        base_config_hash=None,
    )

    with pytest.raises(ConfigConflictError):
        repo.create_snapshot(
            ConfigSnapshot.create(
                paper_run_id=run.paper_run_id,
                config={"version": 2},
                created_by="operator",
                effective_cycle_id="cycle-2",
            ),
            base_config_hash="sha256:stale",
        )


def test_decision_events_are_append_only_and_open_decision_is_idempotent(db_session) -> None:
    run = _create_paper_run(db_session)
    repo = DecisionEventRepository(db_session)
    event = DecisionEvent(
        paper_run_id=run.paper_run_id,
        cycle_id="cycle-1",
        decision_id="decision-1",
        event_type=DecisionEventType.TRADE_INTENT_CREATED,
        block_code=None,
        strategy_id=run.strategy_id,
        strategy_version="v1",
        config_snapshot_id="config-1",
        config_hash="sha256:config",
        symbol="BTC/USDT",
        timeframe="15m",
        candle_close_time=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        payload={"side": "LONG"},
    )

    first = repo.append(event)
    second = repo.append(event)
    blocked = repo.append(
        event.model_copy(
            update={
                "event_id": None,
                "event_type": DecisionEventType.BLOCKED,
                "block_code": BlockCode.DATA_STALE,
            }
        )
    )

    assert first.event_id == second.event_id
    assert blocked.event_id != first.event_id
    assert [item.event_type for item in repo.list_events(paper_run_id=run.paper_run_id)] == [
        DecisionEventType.TRADE_INTENT_CREATED,
        DecisionEventType.BLOCKED,
    ]
