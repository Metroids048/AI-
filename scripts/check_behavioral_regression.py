"""Fail closed when a frozen Golden replay's strategy behavior drifts.

Both inputs are immutable baseline directories produced by
``generate_strategy_golden_baseline.py``.  This command intentionally rejects
coverage-incomplete artifacts: unavailable data cannot prove behavioral
equivalence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.automated_trading.audit.behavior_freeze import compare_golden_behavior


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _load_artifact(directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_json(directory / "BASELINE_MANIFEST.json")
    behavior = _read_json(directory / "behavior.json")
    if manifest.get("status") != "SUFFICIENT" or behavior.get("status") == "UNAVAILABLE":
        raise ValueError(f"behavior artifact is not coverage-sufficient: {directory}")
    active = manifest.get("active_strategy")
    if not isinstance(active, dict):
        raise ValueError(f"active_strategy missing: {directory}")
    identity = {
        "data_hash": manifest.get("data_hash"),
        "source_tree_hash": manifest.get("source_tree_hash"),
        "strategy_key": active.get("strategy_key"),
        "candidate_id": active.get("candidate_id"),
        "candidate_version": active.get("candidate_version"),
        "rules_hash": active.get("rules_hash"),
        "commit_sha": active.get("commit_sha"),
        "manifest_sha256": active.get("manifest_sha256"),
    }
    if any(not isinstance(value, str) or not value for value in identity.values()):
        raise ValueError(f"incomplete behavior identity: {directory}")
    return identity, behavior


def check_behavioral_regression(*, baseline_dir: Path, observed_dir: Path) -> dict[str, Any]:
    """Compare a deterministic replay; identity and behavior must both match."""
    baseline_identity, baseline = _load_artifact(baseline_dir)
    observed_identity, observed = _load_artifact(observed_dir)
    identity_differences = {
        field: {"baseline": baseline_identity[field], "observed": observed_identity[field]}
        for field in baseline_identity
        if baseline_identity[field] != observed_identity[field]
    }
    comparison = compare_golden_behavior(baseline=baseline, observed=observed)
    differences = (
        {"identity": identity_differences, **comparison.differences} if identity_differences else comparison.differences
    )
    return {
        "status": "BEHAVIOR_REGRESSION=FAIL" if differences else comparison.status,
        "baseline_candidate_rate": comparison.baseline_candidate_rate,
        "observed_candidate_rate": comparison.observed_candidate_rate,
        "differences": differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--observed-dir", required=True, type=Path)
    args = parser.parse_args()
    result = check_behavioral_regression(baseline_dir=args.baseline_dir, observed_dir=args.observed_dir)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"] == "BEHAVIOR_REGRESSION=PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
