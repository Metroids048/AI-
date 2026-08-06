from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.verify_legacy_natural_automated_trading_cycle import (
    ExchangeView,
    LegacyNaturalEvidence,
    LegacyNaturalObserver,
    ObservationSnapshot,
    observe,
    scheduler_lane_blockers,
    shadow_runtime_observed,
    write_session,
)


class ReadOnlyFixtureSource:
    """A fixture source deliberately exposing no database or exchange writers."""

    def __init__(self, snapshots: list[ObservationSnapshot], *, blockers: list[str] | None = None) -> None:
        self.snapshots = snapshots
        self.blockers = blockers or []
        self.index = 0
        self.confirmations = {
            "entry-1": {
                "exists": True,
                "status": "closed",
                "trade_ids": ["entry-trade-1"],
                "filled_quantity": "1",
                "average_fill_price": "2000",
                "reduce_only": False,
            },
            "exit-1": {
                "exists": True,
                "status": "closed",
                "trade_ids": ["exit-trade-1"],
                "filled_quantity": "1",
                "reduce_only": True,
            },
        }
        self.calls: list[tuple[str, str]] = []

    def preflight(self) -> list[str]:
        return list(self.blockers)

    def snapshot(self, _symbols: tuple[str, ...]) -> ObservationSnapshot:
        result = deepcopy(self.snapshots[min(self.index, len(self.snapshots) - 1)])
        self.index += 1
        return result

    def confirm_exchange_order(self, *, symbol: str, exchange_order_id: str) -> dict | None:
        self.calls.append((symbol, exchange_order_id))
        return self.confirmations.get(
            exchange_order_id,
            {"exists": True, "status": "open", "trade_ids": [], "filled_quantity": "0", "reduce_only": True},
        )


def _snapshot(*, exchange: ExchangeView | None = None) -> ObservationSnapshot:
    return ObservationSnapshot(
        scheduler_state={"running": True},
        paper_runs=[
            {
                "paper_run_id": "run-1",
                "paper_status": "running",
                "candidate_symbols": ["BTC/USDT", "ETH/USDT"],
                "execution_profile": {
                    "execution_mode": "binance_testnet",
                    "mirror_to_gateway": True,
                    "requested_leverage": 5,
                    "requested_notional": 500,
                },
            }
        ],
        configs={
            "run-1": {
                "config_snapshot_id": "config-1",
                "config_hash": "hash-1",
                "config": {"execution_profile": {"requested_leverage": 5, "requested_notional": 500}},
            }
        },
        orders=[],
        exchange_orders=[],
        fills=[],
        positions=[],
        position_snapshots=[],
        protections=[],
        exchange=exchange or ExchangeView(positions={"BTC/USDT": "0", "ETH/USDT": "0"}),
    )


def _entry_state(*, exchange: ExchangeView | None = None) -> ObservationSnapshot:
    result = _snapshot(exchange=exchange or ExchangeView(positions={"ETH/USDT": "1"}))
    created_at = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    result.orders = [
        {
            "order_execution_id": "local-entry-1",
            "strategy_id": "directional-v1",
            "paper_run_id": "run-1",
            "symbol": "ETH/USDT",
            "execution_status": "filled",
            "close_only_mode": False,
            "order_origin": "live_scheduler",
            "cycle_source": "runtime_scheduler",
            "config_snapshot_id": "config-1",
            "config_hash": "hash-1",
            "gateway_order_id": "entry-1",
            "created_at": created_at,
            "entry_context": {"requested_leverage": 5, "requested_notional": 500},
        }
    ]
    result.fills = [
        {
            "exchange_order_id": "entry-1",
            "cumulative_filled_quantity": "1",
            "average_fill_price": "2000",
        }
    ]
    result.positions = [
        {
            "position_record_id": "position-1",
            "entry_order_id": "local-entry-1",
            "management_status": "MANAGED_STRATEGY",
            "quantity": "1",
            "symbol": "ETH/USDT",
        }
    ]
    return result


def _protected_state() -> ObservationSnapshot:
    result = _entry_state(
        exchange=ExchangeView(positions={"ETH/USDT": "1"}, open_order_ids={"ETH/USDT": {"stop-1", "take-1"}})
    )
    result.protections = [
        {
            "position_record_id": "position-1",
            "status": "ACTIVE",
            "stop_exchange_order_id": "stop-1",
            "take_profit_exchange_order_id": "take-1",
        }
    ]
    return result


def _exit_state() -> ObservationSnapshot:
    result = _protected_state()
    result.orders.append(
        {
            "order_execution_id": "local-exit-1",
            "strategy_id": "directional-v1",
            "paper_run_id": "run-1",
            "symbol": "ETH/USDT",
            "execution_status": "filled",
            "close_only_mode": True,
            "order_origin": "live_scheduler",
            "cycle_source": "runtime_scheduler",
            "gateway_order_id": "exit-1",
            "config_snapshot_id": "config-1",
            "config_hash": "hash-1",
            "created_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
            "entry_context": {"exit_reason": "take_profit"},
        }
    )
    result.exchange_orders = [
        {
            "local_order_execution_id": "local-exit-1",
            "exchange_order_id": "exit-1",
            "reduce_only": True,
        }
    ]
    result.fills.append(
        {
            "exchange_order_id": "exit-1",
            "cumulative_filled_quantity": "1",
            "average_fill_price": "1990",
        }
    )
    return result


def _closed_state() -> ObservationSnapshot:
    result = _exit_state()
    result.exchange = ExchangeView(positions={"ETH/USDT": "0"}, open_order_ids={"ETH/USDT": set()})
    result.positions[0]["management_status"] = "CLOSED"
    result.position_snapshots = [
        {
            "position_record_id": "position-1",
            "quantity": "0",
            "snapshot_time": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
        }
    ]
    return result


def _observer(source: ReadOnlyFixtureSource, evidence: LegacyNaturalEvidence | None = None) -> LegacyNaturalObserver:
    return LegacyNaturalObserver(source, symbols=("BTC/USDT", "ETH/USDT"), evidence=evidence)


def test_preflight_blocked_without_touching_exchange() -> None:
    source = ReadOnlyFixtureSource([_snapshot()], blockers=["BINANCE_AUTO_EXECUTE is false"])
    evidence = _observer(source).start()
    assert evidence.status == "BLOCKED_PREFLIGHT"
    assert source.calls == []


def test_scheduler_lane_preflight_requires_recent_success() -> None:
    now = datetime.now(UTC)
    state = {
        "running": True,
        "last_auto_cycle_at": now.isoformat(),
        "task_run_counts": {"paper_runtime_cycle": 1},
        "task_last_results": {"paper_runtime_cycle": {"paper_runs": 1}},
    }
    assert scheduler_lane_blockers(state, now=now) == []
    state["task_run_counts"]["paper_runtime_cycle"] = 0
    assert "paper_runtime_cycle_not_running" in scheduler_lane_blockers(state, now=now)


def test_shadow_runtime_can_be_proven_by_child_scheduler_state() -> None:
    state = {"task_last_results": {"automated_trading_v2_cycle": {"status": "completed", "v2_activation": "SHADOW"}}}
    assert shadow_runtime_observed(settings_engine="legacy", scheduler_state=state) is True
    assert shadow_runtime_observed(settings_engine="legacy", scheduler_state={}) is False


def test_baseline_skips_manual_btc_and_selects_flat_eth() -> None:
    baseline = _snapshot(exchange=ExchangeView(positions={"BTC/USDT": "0.01", "ETH/USDT": "0"}))
    evidence = _observer(ReadOnlyFixtureSource([baseline])).start()
    assert evidence.status == "WAITING_FOR_NATURAL_ENTRY"
    assert evidence.baseline["selected_symbols"] == ["ETH/USDT"]


def test_baseline_blocks_when_no_symbol_is_clean() -> None:
    baseline = _snapshot(exchange=ExchangeView(positions={"BTC/USDT": "1", "ETH/USDT": "-1"}))
    evidence = _observer(ReadOnlyFixtureSource([baseline])).start()
    assert evidence.status == "BLOCKED_UNCLEAN_BASELINE"


def test_existing_v2_exchange_order_is_a_safety_failure_at_baseline() -> None:
    baseline = _snapshot()
    baseline.v2_exchange_order_ids = {"v2-order-1"}
    evidence = _observer(ReadOnlyFixtureSource([baseline])).start()
    assert evidence.status == "FAILED_SAFETY_VIOLATION"


def test_incomplete_exchange_read_is_never_treated_as_a_flat_baseline() -> None:
    baseline = _snapshot(exchange=ExchangeView(complete=False, error="timeout"))
    evidence = _observer(ReadOnlyFixtureSource([baseline])).start()
    assert evidence.status == "BLOCKED_PREFLIGHT"


def test_full_natural_legacy_lifecycle_passes() -> None:
    source = ReadOnlyFixtureSource([_snapshot(), _entry_state(), _protected_state(), _exit_state(), _closed_state()])
    observer = _observer(source)
    assert observer.start().status == "WAITING_FOR_NATURAL_ENTRY"
    assert observer.poll().status == "POSITION_OPEN"
    assert observer.poll().status == "WAITING_FOR_NATURAL_EXIT"
    assert observer.poll().status == "POSITION_CLOSED"
    evidence = observer.poll()
    assert evidence.passed is True
    assert evidence.proof_type == "LEGACY_NATURAL_SCHEDULER_TESTNET"
    assert evidence.entry_trade_ids == ["entry-trade-1"]
    assert evidence.exit_trade_ids == ["exit-trade-1"]
    assert evidence.v2_shadow_network_orders == 0


def test_entry_without_exchange_id_fails_closed() -> None:
    state = _entry_state()
    state.orders[0]["gateway_order_id"] = None
    observer = _observer(ReadOnlyFixtureSource([_snapshot(), state]))
    observer.start()
    assert observer.poll().status == "FAILED_INCONSISTENT_STATE"


def test_local_and_exchange_fill_mismatch_fails_closed() -> None:
    source = ReadOnlyFixtureSource([_snapshot(), _entry_state()])
    source.confirmations["entry-1"]["filled_quantity"] = "0.5"
    observer = _observer(source)
    observer.start()
    assert observer.poll().status == "FAILED_INCONSISTENT_STATE"


def test_missing_exchange_protection_fails_closed() -> None:
    bad = _protected_state()
    bad.exchange.open_order_ids["ETH/USDT"] = {"stop-1"}
    observer = _observer(ReadOnlyFixtureSource([_snapshot(), _entry_state(), bad]))
    observer.start()
    observer.poll()
    assert observer.poll().status == "FAILED_INCONSISTENT_STATE"


def test_duplicate_stop_and_take_profit_order_ids_fail_closed() -> None:
    bad = _protected_state()
    bad.protections[0]["take_profit_exchange_order_id"] = "stop-1"
    observer = _observer(ReadOnlyFixtureSource([_snapshot(), _entry_state(), bad]))
    observer.start()
    observer.poll()
    assert observer.poll().status == "FAILED_INCONSISTENT_STATE"


def test_manual_order_after_baseline_contaminates_provenance() -> None:
    contaminated = _snapshot()
    contaminated.orders = [
        {
            "order_execution_id": "manual-1",
            "symbol": "ETH/USDT",
            "created_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
            "order_origin": "manual_console",
            "cycle_source": "manual",
        }
    ]
    observer = _observer(ReadOnlyFixtureSource([_snapshot(), contaminated]))
    observer.start()
    assert observer.poll().status == "FAILED_PROVENANCE_CONTAMINATED"


def test_v2_shadow_network_order_is_a_safety_failure() -> None:
    unsafe = _snapshot()
    unsafe.v2_exchange_order_ids = {"v2-order-1"}
    observer = _observer(ReadOnlyFixtureSource([_snapshot(), unsafe]))
    observer.start()
    assert observer.poll().status == "FAILED_SAFETY_VIOLATION"


def test_exit_requires_reduce_only_exchange_confirmation() -> None:
    source = ReadOnlyFixtureSource([_snapshot(), _entry_state(), _protected_state(), _exit_state()])
    source.confirmations["exit-1"]["reduce_only"] = False
    observer = _observer(source)
    observer.start()
    observer.poll()
    observer.poll()
    assert observer.poll().status == "FAILED_INCONSISTENT_STATE"


def test_exit_without_local_fill_receipt_fails_closed() -> None:
    bad = _exit_state()
    bad.fills = [fill for fill in bad.fills if fill["exchange_order_id"] != "exit-1"]
    observer = _observer(ReadOnlyFixtureSource([_snapshot(), _entry_state(), _protected_state(), bad]))
    observer.start()
    observer.poll()
    observer.poll()
    assert observer.poll().status == "FAILED_INCONSISTENT_STATE"


def test_entry_operator_profile_mismatch_fails_closed() -> None:
    bad = _entry_state()
    bad.orders[0]["entry_context"]["requested_leverage"] = 10
    observer = _observer(ReadOnlyFixtureSource([_snapshot(), bad]))
    observer.start()
    assert observer.poll().status == "FAILED_INCONSISTENT_STATE"


@pytest.mark.parametrize("phase", ["entry", "exit"])
def test_timeout_reports_the_correct_resumable_result(phase: str) -> None:
    source = ReadOnlyFixtureSource([_snapshot()])
    observer = _observer(source)
    observer.start()
    if phase == "exit":
        observer.evidence.entry_exchange_order_id = "entry-1"
    evidence = observer.finish_timeout()
    assert evidence.status == ("NO_NATURAL_ENTRY_OBSERVED" if phase == "entry" else "ENTRY_OBSERVED_EXIT_PENDING")


def test_resume_rejects_changed_config(monkeypatch) -> None:
    evidence = LegacyNaturalEvidence(
        git_commit="same-commit",
        started_at=datetime.now(UTC).isoformat(),
        paper_run_id="run-1",
        config_snapshot_hash="old-hash",
    )
    monkeypatch.setattr("scripts.verify_legacy_natural_automated_trading_cycle._git_commit", lambda: "same-commit")
    observer = _observer(ReadOnlyFixtureSource([_snapshot()]), evidence=evidence)
    assert observer.resume().status == "FAILED_PROVENANCE_CONTAMINATED"


def test_resume_rechecks_preflight_before_continuing(monkeypatch) -> None:
    evidence = LegacyNaturalEvidence(git_commit="same-commit", started_at=datetime.now(UTC).isoformat())
    monkeypatch.setattr("scripts.verify_legacy_natural_automated_trading_cycle._git_commit", lambda: "same-commit")
    source = ReadOnlyFixtureSource([_snapshot()], blockers=["scheduler_heartbeat_stale"])
    observer = _observer(source, evidence=evidence)
    assert observer.resume().status == "BLOCKED_PREFLIGHT"


def test_session_serialisation_has_no_credentials(tmp_path: Path) -> None:
    evidence = LegacyNaturalEvidence(git_commit="abc", started_at=datetime.now(UTC).isoformat())
    path = write_session(evidence, tmp_path)
    payload = json.dumps(json.loads(path.read_text(encoding="utf-8"))).lower()
    assert "api_key" not in payload
    assert "api_secret" not in payload


def test_observe_persists_a_resumable_no_entry_session(tmp_path: Path) -> None:
    source = ReadOnlyFixtureSource([_snapshot()])
    observer = _observer(source)
    evidence = observe(
        observer,
        output_dir=tmp_path,
        entry_timeout=timedelta(seconds=0),
        lifecycle_timeout=timedelta(seconds=0),
        poll_seconds=1,
    )
    assert evidence.status == "NO_NATURAL_ENTRY_OBSERVED"
    assert list(tmp_path.glob("legacy-natural-proof-session-*.json"))
