from __future__ import annotations

import os
import runpy
from pathlib import Path


def test_isolated_scheduler_enforces_v2_testnet_writer_environment(
    monkeypatch,
) -> None:
    for name in (
        "POSTGRES_URL",
        "APP_ENV",
        "AUTOMATED_TRADING_ENGINE",
        "BINANCE_USE_TESTNET",
        "LIVE_TRADING_ENABLED",
        "BINANCE_LIVE_WS_ENABLED",
        "BINANCE_HTTPS_PROXY",
        "BINANCE_HTTP_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run-local-paper-scheduler.py"))
    namespace["configure_scheduler_environment"]("sqlite:///C:/runtime.db")

    assert os.environ["POSTGRES_URL"] == "sqlite:///C:/runtime.db"
    assert os.environ["APP_ENV"] == "development"
    assert os.environ["AUTOMATED_TRADING_ENGINE"] == "v2_active"
    assert os.environ["BINANCE_USE_TESTNET"] == "true"
    assert os.environ["LIVE_TRADING_ENABLED"] == "false"
    assert os.environ["BINANCE_LIVE_WS_ENABLED"] == "false"
