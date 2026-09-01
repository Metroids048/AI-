from __future__ import annotations

from dataclasses import dataclass

from services.execution import v2_scheduler_entry


def test_symbol_settings_capture_active_snapshot_once_per_scheduler_cycle(monkeypatch) -> None:
    @dataclass(frozen=True)
    class FakeSettings:
        symbol: str
        active_snapshot_id: str | None = None
        active_snapshot_config: dict | None = None
        active_snapshot_hash: str | None = None

    loads: list[str] = []
    captured = v2_scheduler_entry._V2OperatorSnapshot(
        execution_profile={"symbols": {}},
        active_snapshot_id="snapshot-shared",
        active_snapshot_config={"execution_profile": {"symbols": {}}},
        active_snapshot_hash="sha256:shared",
    )

    def load_once(*, cycle_id: str):
        loads.append(cycle_id)
        return captured

    monkeypatch.setattr(v2_scheduler_entry, "_load_v2_operator_snapshot", load_once)
    monkeypatch.setattr(
        v2_scheduler_entry,
        "resolve_v2_execution_settings",
        lambda symbol, _profile: FakeSettings(symbol=symbol),
    )

    settings = v2_scheduler_entry._load_v2_operator_execution_settings_for_symbols(
        symbols=("BTC/USDT", "ETH/USDT"),
        cycle_id="scheduler-cycle-1",
    )

    assert loads == ["scheduler-cycle-1"]
    assert settings["BTC/USDT"].active_snapshot_id == "snapshot-shared"
    assert settings["ETH/USDT"].active_snapshot_id == "snapshot-shared"
    assert settings["BTC/USDT"].active_snapshot_hash == settings["ETH/USDT"].active_snapshot_hash
