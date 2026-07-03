from __future__ import annotations

from pathlib import Path


def test_status_matrix_references_existing_core_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    required_paths = [
        "shared/models/backtest.py",
        "services/validation/walk_forward.py",
        "services/validation/stress_scenarios.py",
        "apps/api/routers/system.py",
        "apps/api/routers/notifications.py",
        "services/data/capabilities.py",
    ]
    for path in required_paths:
        assert (root / path).exists(), path
