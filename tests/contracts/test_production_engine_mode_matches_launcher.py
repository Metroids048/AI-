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
        (
            "scripts/launch-paper-console.ps1",
            r'\$env:AUTOMATED_TRADING_ENGINE\s*=\s*"([^"]+)"',
        ),
    ],
)
def test_production_launchers_pin_v2_shadow(relative_path: str, pattern: str) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert re.findall(pattern, source) == ["v2_shadow"]
