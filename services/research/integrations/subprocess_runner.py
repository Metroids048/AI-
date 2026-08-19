"""Narrow subprocess boundary for read-only research engines."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

_CREDENTIAL_MARKERS = ("BINANCE", "API_KEY", "API_SECRET", "SECRET_KEY", "PRIVATE_KEY", "TOKEN")


def research_environment() -> dict[str, str]:
    """Return an environment that cannot carry exchange credentials downstream."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _CREDENTIAL_MARKERS)
    }
    environment["RESEARCH_ONLY_MODE"] = "true"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def run_research_subprocess(
    command: Sequence[str], *, timeout_seconds: float = 900.0, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a finite research command with no shell and no inherited credentials."""
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        cwd=str(cwd) if cwd is not None else None,
        env=research_environment(),
        text=True,
        timeout=timeout_seconds,
    )
