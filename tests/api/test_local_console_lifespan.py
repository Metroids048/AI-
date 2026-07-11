from __future__ import annotations


def test_local_console_api_only_is_explicit_opt_in(monkeypatch) -> None:
    from apps.api.main import _is_local_console_api_only

    monkeypatch.setenv("PAPER_CONSOLE_API_ONLY", "true")
    assert _is_local_console_api_only() is True

    monkeypatch.setenv("PAPER_CONSOLE_API_ONLY", "false")
    assert _is_local_console_api_only() is False
