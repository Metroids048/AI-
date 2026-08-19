from __future__ import annotations

import json
from pathlib import Path

from scripts.check_behavioral_regression import check_behavioral_regression


def _write_artifact(directory: Path, *, candidates: int = 22, package_hash: str = "p" * 64) -> None:
    directory.mkdir()
    (directory / "BASELINE_MANIFEST.json").write_text(
        json.dumps(
            {
                "status": "SUFFICIENT",
                "data_hash": "data-hash",
                "source_tree_hash": "tree-hash",
                "active_strategy": {
                    "strategy_key": "auto_paper_mature_templates",
                    "candidate_id": "trend_momentum_v2_enriched",
                    "candidate_version": "2.0.0",
                    "rules_hash": "r" * 64,
                    "strategy_code_hash": "c" * 64,
                    "strategy_package_hash": package_hash,
                    "strategy_source_commit": "a" * 40,
                    "approval_commit": None,
                    "manifest_sha256": "manifest-hash",
                },
            }
        ),
        encoding="utf-8",
    )
    (directory / "behavior.json").write_text(
        json.dumps(
            {
                "unique_closed_bar_decisions": 100,
                "signals": 42,
                "candidates": candidates,
                "reason_distribution": {"CANDIDATE_READY": candidates, "NO_SIGNAL": 58},
                "directions": {"LONG": 12, "SHORT": 10},
                "stop_geometry": {"atr_multiple": "2"},
                "target_geometry": {"risk_reward": "2"},
                "dry_run_intents": candidates,
            }
        ),
        encoding="utf-8",
    )


def test_behavioral_regression_command_fails_on_candidate_starvation(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    observed_dir = tmp_path / "observed"
    _write_artifact(baseline_dir, candidates=22)
    _write_artifact(observed_dir, candidates=1)

    result = check_behavioral_regression(baseline_dir=baseline_dir, observed_dir=observed_dir)

    assert result["status"] == "BEHAVIOR_REGRESSION=FAIL"
    assert result["differences"]["candidates"] == {"baseline": 22, "observed": 1}


def test_behavioral_regression_command_fails_on_strategy_package_change(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    observed_dir = tmp_path / "observed"
    _write_artifact(baseline_dir)
    _write_artifact(observed_dir, package_hash="q" * 64)

    result = check_behavioral_regression(baseline_dir=baseline_dir, observed_dir=observed_dir)

    assert result["status"] == "BEHAVIOR_REGRESSION=FAIL"
    assert result["differences"]["identity"]["strategy_package_hash"] == {"baseline": "p" * 64, "observed": "q" * 64}
