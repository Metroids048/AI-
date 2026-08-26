from __future__ import annotations

import json
from pathlib import Path

from scripts import run_alpha_research_recovery_v5 as v5


def test_history_gate_requires_one_real_source_with_both_minimums() -> None:
    assert not v5._gate_passed(
        [
            {
                "history_accessible": True,
                "first_message_at": "2025-01-01T00:00:00+00:00",
                "last_message_at": "2025-06-29T00:00:00+00:00",
                "signal_like_message_count": 99,
            }
        ]
    )
    assert v5._gate_passed(
        [
            {
                "history_accessible": True,
                "first_message_at": "2025-01-01T00:00:00+00:00",
                "last_message_at": "2025-06-30T00:00:00+00:00",
                "signal_like_message_count": 100,
            }
        ]
    )


def test_blocked_artifacts_stop_all_research_and_keep_runtime_frozen(tmp_path: Path) -> None:
    audit = {
        "status": v5.STATUS_BLOCKED,
        "blocker": "TELEGRAM_CREDENTIALS_NOT_CONFIGURED",
        "accessible_groups": [],
    }
    report = v5.write_blocked_artifacts(output_dir=tmp_path, audit=audit)
    assert report["status"] == v5.STATUS_BLOCKED
    assert report["final_holdout_accessed"] is False
    assert report["runtime_modified"] is False
    assert report["production"] == "NOT_GRANTED"
    loaded = json.loads((tmp_path / "FINAL_REPORT.json").read_text(encoding="utf-8"))
    assert loaded["h1"]["status"] == "NOT_RUN"
    assert loaded["h2"]["status"] == "NOT_RUN"
    assert loaded["h3"]["status"] == "NOT_RUN"
    assert (tmp_path / "SIGNAL_LEDGER.parquet").exists()


def test_config_audit_does_not_expose_secret_values() -> None:
    payload = v5._config_audit()
    serialized = json.dumps(payload)
    assert "api_hash" not in serialized
    assert "phone" not in serialized
    assert "token" not in serialized.lower()
