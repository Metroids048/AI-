"""AWF-001..024 account-scoped writer and mutation fence contracts."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from services.automated_trading.infrastructure.account_writer import (
    AccountWriterFenceError,
    acquire_account_writer,
    bind_account,
    capability_is_current,
    database_identity,
    mutation_guard,
    rebind_account,
    registry_path,
    release_account_writer,
    renew_account_writer,
    resolve_account_scope,
)

SCOPE = "BINANCE:TESTNET:test-account"


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    path = tmp_path / "account-writer.json"
    monkeypatch.setenv("V2_ACCOUNT_WRITER_REGISTRY_PATH", str(path))
    return path


def _bind(path, scope=SCOPE, db="db-a"):
    bind_account(
        account_scope_key=scope,
        database_id=db,
        operator_identity="operator-test",
        operator_reason="AWF contract setup",
    )


def test_awf_001_same_database_second_supervisor_is_blocked(registry):
    _bind(registry)
    acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a")
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_WRITER_ALREADY_HELD"):
        acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-b")


def test_awf_002_different_database_same_account_is_blocked(registry):
    _bind(registry)
    acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a")
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_BOUND_TO_DIFFERENT_DATABASE"):
        acquire_account_writer(account_scope_key=SCOPE, database_id="db-b", owner_id="owner-b")


def test_awf_003_local_lease_cannot_bypass_account_fence(registry):
    _bind(registry)
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_BOUND_TO_DIFFERENT_DATABASE"):
        acquire_account_writer(account_scope_key=SCOPE, database_id="db-b", owner_id="local-owner")


def test_awf_004_same_database_expired_lease_takes_over_with_next_generation(registry):
    _bind(registry)
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a", lease_seconds=0)
    second = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-b")
    assert second.capability.generation == first.capability.generation + 1


def test_awf_005_different_database_cannot_take_over_expired_binding(registry):
    _bind(registry)
    acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a", lease_seconds=0)
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_BOUND_TO_DIFFERENT_DATABASE"):
        acquire_account_writer(account_scope_key=SCOPE, database_id="db-b", owner_id="owner-b")


def test_awf_006_old_owner_cannot_renew_after_takeover(registry):
    _bind(registry)
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a", lease_seconds=0)
    acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-b")
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_WRITER_FENCE_REJECTED"):
        renew_account_writer(first)


def test_awf_007_old_owner_cannot_release_new_owner(registry):
    _bind(registry)
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a", lease_seconds=0)
    acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-b")
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_WRITER_FENCE_REJECTED"):
        release_account_writer(first)


@pytest.mark.parametrize("scar", ["008", "009", "010", "011", "012", "013", "014", "017"])
def test_awf_stale_generation_cannot_mutate(scar, registry):
    _bind(registry)
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a", lease_seconds=0)
    acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-b")
    with (
        pytest.raises(AccountWriterFenceError, match="ACCOUNT_WRITER_FENCE_REJECTED"),
        mutation_guard(first.capability),
    ):
        raise AssertionError("stale capability entered mutation boundary")


def test_awf_015_read_only_access_does_not_require_capability(registry):
    assert registry_path() == registry
    assert database_identity("sqlite:///C:/tmp/a.db") != database_identity("sqlite:///C:/tmp/b.db")


def test_awf_016_same_supervisor_renewal_retains_generation(registry):
    _bind(registry)
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a")
    renewed = renew_account_writer(first)
    assert renewed.capability.generation == first.capability.generation


def test_awf_018_alternate_database_is_fail_closed(registry):
    _bind(registry)
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_BOUND_TO_DIFFERENT_DATABASE"):
        acquire_account_writer(account_scope_key=SCOPE, database_id="alternate-db", owner_id="alternate")


def test_awf_019_different_account_scope_operates_independently(registry):
    _bind(registry)
    other = "BINANCE:TESTNET:other-account"
    _bind(registry, scope=other, db="db-b")
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a")
    second = acquire_account_writer(account_scope_key=other, database_id="db-b", owner_id="owner-b")
    assert first.capability.account_scope_key != second.capability.account_scope_key


def test_awf_020_testnet_and_mainnet_scopes_do_not_share(registry):
    _bind(registry)
    mainnet = "BINANCE:MAINNET:test-account"
    _bind(registry, scope=mainnet, db="db-a")
    testnet_lease = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="testnet")
    mainnet_lease = acquire_account_writer(account_scope_key=mainnet, database_id="db-a", owner_id="mainnet")
    assert testnet_lease.capability.account_scope_key != mainnet_lease.capability.account_scope_key


def test_awf_021_missing_scope_blocks_resolution(registry, monkeypatch):
    monkeypatch.delenv("BINANCE_ACCOUNT_SCOPE_ID", raising=False)
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_SCOPE_IDENTITY_MISSING"):
        resolve_account_scope()


def test_awf_022_binding_is_required_before_first_writer(registry):
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_BINDING_REQUIRED"):
        acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a")


def test_awf_023_rebind_is_explicit_and_audited(registry):
    _bind(registry)
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a", lease_seconds=0)
    result = rebind_account(
        account_scope_key=SCOPE,
        database_id="db-b",
        operator_identity="operator-test",
        operator_reason="planned canonical database move",
        exchange_is_flat=lambda: True,
        exchange_open_orders_empty=lambda: True,
        new_database_recovery_clear=lambda: True,
    )
    assert result["bound_database_identity"] == "db-b"
    assert json.loads(registry.read_text())["accounts"][SCOPE]["operator_binding_metadata"]["operator_reason"]
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_WRITER_FENCE_REJECTED"):
        renew_account_writer(first)


def test_awf_024_generation_cannot_switch_during_inflight_mutation(registry):
    _bind(registry)
    first = acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-a")
    entered = threading.Event()
    outcome = []

    def stale_takeover():
        entered.wait(timeout=2)
        try:
            acquire_account_writer(account_scope_key=SCOPE, database_id="db-a", owner_id="owner-b")
        except AccountWriterFenceError as exc:
            outcome.append(exc.code)

    thread = threading.Thread(target=stale_takeover)
    thread.start()
    with mutation_guard(first.capability):
        entered.set()
        time.sleep(0.05)
        assert not outcome
    thread.join(timeout=2)
    assert outcome == ["ACCOUNT_WRITER_ALREADY_HELD"]


def test_awf_rejects_non_capability_without_touching_mock_path(registry):
    fake = MagicMock()
    assert not capability_is_current(fake)
    with pytest.raises(AccountWriterFenceError, match="ACCOUNT_WRITER_FENCE_REJECTED"), mutation_guard(fake):
        pass
