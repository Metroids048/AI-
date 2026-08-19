from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():  # noqa: ANN202
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_testnet_canary_acceptance.py"
    spec = importlib.util.spec_from_file_location("run_testnet_canary_acceptance", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canary_command_refuses_without_explicit_confirmation(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr("sys.argv", ["run_testnet_canary_acceptance.py"])

    with pytest.raises(SystemExit, match="2"):
        module.main()


def test_canary_command_marks_cycle_as_explicit_and_non_default(monkeypatch, capsys) -> None:
    module = _module()
    captured = {}
    monkeypatch.setattr(
        "sys.argv",
        ["run_testnet_canary_acceptance.py", "--confirm-testnet-canary", "--symbol", "BTC/USDT"],
    )
    monkeypatch.setattr(
        module,
        "execute_v2_automated_trading_cycles",
        lambda payload: captured.update(payload) or {"status": "completed"},
    )

    assert module.main() == 0

    assert captured == {
        "canary_acceptance": True,
        "symbols": ["BTC/USDT"],
        "cycle_source": "explicit_testnet_canary_acceptance",
    }
    assert '"status": "completed"' in capsys.readouterr().out
