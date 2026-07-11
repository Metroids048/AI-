from __future__ import annotations

import os


def test_local_console_flag_sets_process_isolation(monkeypatch) -> None:
    from apps.api import local_server

    monkeypatch.delenv("PAPER_CONSOLE_API_ONLY", raising=False)
    monkeypatch.delenv("RUNTIME_SCHEDULER_AUTOSTART", raising=False)
    monkeypatch.delenv("BINANCE_LIVE_WS_ENABLED", raising=False)
    monkeypatch.setattr("sys.argv", ["local_server", "--local-console"])
    monkeypatch.setattr(local_server.uvicorn, "run", lambda *args, **kwargs: None)

    local_server.main()

    assert os.environ["PAPER_CONSOLE_API_ONLY"] == "true"
    assert os.environ["RUNTIME_SCHEDULER_AUTOSTART"] == "false"
    assert os.environ["BINANCE_LIVE_WS_ENABLED"] == "false"
