from __future__ import annotations

import json

import pytest

from services.automated_trading.application.canonical_strategy_manifest import (
    ManifestValidationError,
    load_canonical_strategy_manifest,
)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 4,
        "strategy_key": "auto_paper_mature_templates",
        "strategy_id": "trend_momentum_v2_enriched",
        "strategy_version": "2.0.0",
        "rules_hash": "a" * 64,
        "commit_sha": "b" * 40,
        "configured_execution_scope": ["BTC/USDT", "ETH/USDT"],
        "eligible_execution_symbols": [],
        "research_symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"],
        "validation_evidence": {"dataset_hash": None, "conclusion": "STRATEGY_NOT_READY"},
        "golden_behavior_ref": None,
        "authorization_state": "PENDING",
        "approval": {"approved_by": None, "approved_at": None, "rationale": None},
        "config_snapshot_hash": None,
        "effective_at": "2026-08-19T00:00:00+00:00",
    }


def test_manifest_v4_exposes_distinct_execution_and_research_scopes(tmp_path) -> None:
    path = tmp_path / "active.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")

    manifest = load_canonical_strategy_manifest(path)

    assert manifest.configured_execution_scope == ("BTC/USDT", "ETH/USDT")
    assert manifest.eligible_execution_symbols == ()
    assert manifest.research_symbols == ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT")
    assert manifest.authorization_state == "PENDING"


def test_manifest_rejects_execution_symbol_outside_configured_scope(tmp_path) -> None:
    payload = _manifest()
    payload["eligible_execution_symbols"] = ["BTC/USDT", "SOL/USDT"]
    path = tmp_path / "active.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="configured execution scope"):
        load_canonical_strategy_manifest(path)
