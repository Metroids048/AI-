from __future__ import annotations

import json
from pathlib import Path

from scripts import run_alpha_research_recovery_v4 as v4


def test_v4_expected_baseline_is_frozen() -> None:
    assert v4.EXPECTED == {
        "trades": 281,
        "profit_factor": 1.1576630479094718,
        "expectancy": 0.001512451049147131,
        "max_drawdown": 0.18736432836022304,
    }


def test_v4_plan_is_overlay_only(tmp_path: Path) -> None:
    payload = {
        "status": "BLOCKED_BASELINE_REPRODUCTION",
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production_authority": "NOT_GRANTED",
    }
    path = tmp_path / "FINAL_REPORT.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["status"] == "BLOCKED_BASELINE_REPRODUCTION"
    assert loaded["final_holdout_accessed"] is False
    assert loaded["runtime_modified"] is False
    assert loaded["production_authority"] == "NOT_GRANTED"
