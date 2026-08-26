from __future__ import annotations

import json
from pathlib import Path

from scripts import run_alpha_research_recovery_v6 as v6


def test_iter_windows_is_narrow_and_excludes_holdout() -> None:
    windows = list(v6.iter_windows(v6.RESEARCH_START, v6.FINAL_HOLDOUT_START, days=7))
    assert windows
    assert all((end - start).days <= 7 for start, end in windows)
    assert windows[-1][1] == v6.FINAL_HOLDOUT_START


def test_blocked_artifacts_keep_hypotheses_and_runtime_frozen(tmp_path: Path) -> None:
    report = v6.write_blocked_artifacts(
        output_dir=tmp_path,
        audits=[{"source": "GDELT_DOC_2.0", "event_count": 0, "usable": False}],
        blocker="GDELT_HISTORICAL_SOURCE_UNAVAILABLE",
    )
    assert report["status"] == v6.STATUS_BLOCKED
    assert report["runtime_modified"] is False
    assert report["final_holdout_accessed"] is False
    plan = json.loads((tmp_path / "RESEARCH_PLAN.json").read_text(encoding="utf-8"))
    assert plan["hypotheses"] == [
        "REGULATORY_ETF_EVENT_V1",
        "SECURITY_EXCHANGE_SHOCK_V1",
        "NEWS_ATTENTION_MOMENTUM_V1",
    ]
    assert (tmp_path / "EVENT_CLUSTER_LEDGER.parquet").exists()


def test_gdelt_query_uses_explicit_point_in_time_window() -> None:
    url = v6._window_query(v6.RESEARCH_START, v6.RESEARCH_START.replace(day=30))
    assert "startdatetime=20230129000000" in url
    assert "enddatetime=20230130000000" in url
    assert "maxrecords=250" in url
