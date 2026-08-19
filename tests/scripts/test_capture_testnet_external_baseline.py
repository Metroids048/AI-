from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_capture_script():  # noqa: ANN202
    path = Path(__file__).resolve().parents[2] / "scripts" / "capture_testnet_external_baseline.py"
    spec = importlib.util.spec_from_file_location("capture_testnet_external_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_persisted_external_baseline_survives_scheduler_state_cleanup(tmp_path, monkeypatch) -> None:
    """A fresh launcher must not depend on its former scheduler process environment."""
    capture = _load_capture_script()
    baseline_path = tmp_path / ".local" / "testnet-external-baseline.json"
    scheduler_state = tmp_path / "logs" / "scheduler-state.json"
    scheduler_state.parent.mkdir()
    scheduler_state.write_text('{"external_baseline_captured": true}', encoding="utf-8")

    capture.persist_external_baseline({"ETH/USDT:short": "10.976"}, path=baseline_path)

    # The old scheduler and its only runtime state are gone before the next
    # normal launcher starts, and no Codex-provided baseline environment remains.
    scheduler_state.unlink()
    monkeypatch.delenv("V2_EXTERNAL_BASELINE_JSON", raising=False)
    monkeypatch.delenv("V2_ALLOW_UNMANAGED_EXTERNAL_POSITIONS", raising=False)

    monkeypatch.setattr(capture, "capture_baseline", lambda: {"ETH/USDT:short": "10.976"})
    assert capture.require_persisted_external_baseline(path=baseline_path) == {"ETH/USDT:short": "10.976"}


def test_manual_baseline_lifecycle_reports_drift_without_rewriting(tmp_path, monkeypatch) -> None:
    capture = _load_capture_script()
    baseline_path = tmp_path / ".local" / "testnet-external-baseline.json"
    capture.persist_external_baseline({"BTC/USDT:short": "0.5346"}, path=baseline_path)
    monkeypatch.setattr(capture, "capture_baseline", lambda: {})

    lifecycle = capture.inspect_baseline_lifecycle(path=baseline_path)

    assert lifecycle["status"] == "MANUAL_BASELINE_DRIFT"
    assert lifecycle["acknowledgement_status"] == "MANUAL_BASELINE_ACK_REQUIRED"
    assert lifecycle["drift_keys"] == ["BTC/USDT:short"]
    assert capture.load_persisted_external_baseline(path=baseline_path) == {"BTC/USDT:short": "0.5346"}
    assert capture.require_persisted_external_baseline(path=baseline_path, allow_manual_drift=True) == {
        "BTC/USDT:short": "0.5346"
    }


def test_manual_baseline_refresh_requires_explicit_audit_and_is_atomic(tmp_path, monkeypatch) -> None:
    capture = _load_capture_script()
    baseline_path = tmp_path / ".local" / "testnet-external-baseline.json"
    capture.persist_external_baseline({"BTC/USDT:short": "0.5346"}, path=baseline_path)
    monkeypatch.setattr(capture, "capture_baseline", lambda: {})

    with pytest.raises(RuntimeError, match="BASELINE_ACKNOWLEDGEMENT_REQUIRES_OPERATOR_AND_REASON"):
        capture.acknowledge_baseline_refresh(operator="", reason="", path=baseline_path)
    audit = capture.acknowledge_baseline_refresh(
        operator="operator-test",
        reason="manual BTC close acknowledged",
        path=baseline_path,
    )

    assert audit["status"] == "BASELINE_REFRESHED"
    record = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert record["positions"] == {}
    assert record["last_acknowledgement"]["previous_positions"] == {"BTC/USDT:short": "0.5346"}
    assert record["last_acknowledgement"]["new_positions"] == {}


def test_unpersisted_external_position_fails_closed(tmp_path, monkeypatch) -> None:
    capture = _load_capture_script()
    monkeypatch.setattr(capture, "capture_baseline", lambda: {"ETH/USDT:short": "10.976"})

    with pytest.raises(RuntimeError, match="EXTERNAL_BASELINE_NOT_PERSISTED"):
        capture.require_persisted_external_baseline(path=tmp_path / ".local" / "testnet-external-baseline.json")


def test_empty_baseline_can_be_persisted(tmp_path, monkeypatch) -> None:
    """Zero unmanaged exposure is a real capture result, not a failed capture.

    File absence is the only evidence of "never captured"; an empty object is
    positive evidence that capture succeeded and found no external exposure.
    """
    capture = _load_capture_script()
    baseline_path = tmp_path / ".local" / "testnet-external-baseline.json"

    written = capture.persist_external_baseline({}, path=baseline_path)

    assert written == baseline_path
    assert baseline_path.exists()
    record = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert record["execution_mode"] == "BINANCE_TESTNET"
    assert record["positions"] == {}
    assert capture.load_persisted_external_baseline(path=baseline_path) == {}

    monkeypatch.setattr(capture, "capture_baseline", lambda: {})
    assert capture.require_persisted_external_baseline(path=baseline_path) == {}


def test_empty_legacy_scope_baseline_is_migrated_only_after_fresh_flat_capture(tmp_path, monkeypatch) -> None:
    """Shrinking the manifest scope must not require an unsafe manual rewrite."""
    capture = _load_capture_script()
    baseline_path = tmp_path / ".local" / "testnet-external-baseline.json"
    baseline_path.parent.mkdir()
    legacy_scope = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"]
    baseline_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "execution_mode": "BINANCE_TESTNET",
                "captured_symbols": legacy_scope,
                "source": "binance_testnet_authoritative_snapshot",
                "positions": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(capture, "capture_baseline", lambda: {})

    assert capture.require_persisted_external_baseline(path=baseline_path) == {}

    migrated = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert migrated["captured_symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert migrated["last_acknowledgement"]["status"] == "SCOPE_MIGRATED_EMPTY_BASELINE"
    assert migrated["last_acknowledgement"]["previous_symbols"] == legacy_scope


def test_missing_baseline_fails_closed_even_at_zero_exposure(tmp_path, monkeypatch) -> None:
    """Zero observed exposure must not be mistaken for a persisted capture."""
    capture = _load_capture_script()
    monkeypatch.setattr(capture, "capture_baseline", lambda: {})

    with pytest.raises(RuntimeError, match="EXTERNAL_BASELINE_NOT_PERSISTED"):
        capture.require_persisted_external_baseline(path=tmp_path / ".local" / "testnet-external-baseline.json")


def test_persisted_baseline_still_rejects_invalid_shapes(tmp_path) -> None:
    """Widening five-symbol scope must not admit malformed baseline payloads."""
    capture = _load_capture_script()
    baseline_path = tmp_path / ".local" / "testnet-external-baseline.json"

    invalid_payloads: tuple[object, ...] = (None, [], "", 0)
    for invalid in invalid_payloads:
        with pytest.raises(RuntimeError, match="EXTERNAL_BASELINE_EMPTY_OR_INVALID"):
            capture.persist_external_baseline(invalid, path=baseline_path)
    for invalid_key in (
        "DOGE/USDT:long",
        "SOL/USDT:flat",
        "SOLUSDT:long",
        "SOL/USDT:long;DROP TABLE positions",
        "SOL/USDT:long",
        "XRP/USDT:short",
    ):
        with pytest.raises(RuntimeError, match="EXTERNAL_BASELINE_INVALID_KEY"):
            capture.persist_external_baseline({invalid_key: "1"}, path=baseline_path)


@pytest.mark.parametrize("quantity", ("0", "-1"))
def test_external_baseline_rejects_non_positive_quantity(tmp_path, quantity: str) -> None:
    capture = _load_capture_script()

    with pytest.raises(RuntimeError, match="EXTERNAL_BASELINE_INVALID_QUANTITY"):
        capture.persist_external_baseline(
            {"BTC/USDT:long": quantity},
            path=tmp_path / ".local" / "testnet-external-baseline.json",
        )
