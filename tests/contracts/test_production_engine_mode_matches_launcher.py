from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative_path", "pattern"),
    [
        (
            "scripts/run-local-paper-scheduler.py",
            r'os\.environ\["AUTOMATED_TRADING_ENGINE"\]\s*=\s*"([^"]+)"',
        ),
    ],
)
def test_explicit_development_scheduler_keeps_v2_shadow_default(relative_path: str, pattern: str) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert re.findall(pattern, source) == ["v2_shadow"]


def test_one_click_launcher_requests_the_active_testnet_contract() -> None:
    source = (ROOT / "一键启动.cmd").read_text(encoding="utf-8")

    assert source.count("-AutomatedTradingEngine v2_active") == 2
    assert source.count("-EnableNaturalTestnet") == 2
    assert source.count("-PreserveExternalTestnetBaseline") == 2


def test_powershell_launcher_keeps_shadow_default_and_checks_actual_state() -> None:
    source = (ROOT / "scripts" / "launch-paper-console.ps1").read_text(encoding="utf-8")

    assert '[string]$AutomatedTradingEngine = "v2_shadow"' in source
    assert "services.execution.runtime_state" in source
    assert "--require-active-contract" in source
    assert "Test-ActiveTradingModeContract" in source
    assert "Remove-Item -LiteralPath $SchedulerStateFile" in source
    assert "if (-not (Test-SchedulerHealthy))" in source
