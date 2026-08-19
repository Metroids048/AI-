from __future__ import annotations

import json
from pathlib import Path

from scripts.check_behavioral_regression import check_behavioral_regression


def _write_artifact(directory: Path, *, candidates: int = 22, commit_sha: str = "a" * 40) -> None:
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
                    "commit_sha": commit_sha,
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


def test_behavioral_regression_command_fails_on_manifest_commit_change(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    observed_dir = tmp_path / "observed"
    _write_artifact(baseline_dir)
    _write_artifact(observed_dir, commit_sha="b" * 40)

    result = check_behavioral_regression(baseline_dir=baseline_dir, observed_dir=observed_dir)

    assert result["status"] == "BEHAVIOR_REGRESSION=FAIL"
    assert result["differences"]["identity"]["commit_sha"] == {"baseline": "a" * 40, "observed": "b" * 40}
