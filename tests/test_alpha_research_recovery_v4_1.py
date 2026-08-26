from pathlib import Path

from scripts.run_alpha_research_recovery_v4_1 import build_report


def test_v4_1_recovers_champion_baseline_without_touching_holdout(tmp_path: Path) -> None:
    report = build_report(database=Path(".strategy_refactor_history.db"), output=tmp_path)

    assert report["status"] == "BASELINE_REPRODUCED"
    assert report["root_cause"] == "REPLAY_SCOPE_SEMANTICS_MISMATCH"
    assert report["reproduction_matrix"]["R3_proposal_replay_provenance"]["trades"] == 281
    assert report["reproduction_matrix"]["R3_proposal_replay_provenance"]["btc_trades"] == 132
    assert report["reproduction_matrix"]["R3_proposal_replay_provenance"]["eth_trades"] == 149
    assert report["final_holdout_accessed"] is False
    assert report["runtime_modified"] is False
    assert (tmp_path / "BASELINE_EVENT_LEDGER.parquet").is_file()


def test_v4_event_set_is_distinct_from_champion_event_set(tmp_path: Path) -> None:
    report = build_report(database=Path(".strategy_refactor_history.db"), output=tmp_path)
    diff = report["event_set_diff"]

    assert diff["historical_unique"] == 281
    assert diff["v4_unique"] == 732
    assert diff["overlap"] == 0
